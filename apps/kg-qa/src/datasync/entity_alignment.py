import hashlib
import logging
import re
import unicodedata

import chromadb
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from tqdm import tqdm
from sklearn.cluster import DBSCAN
from collections import defaultdict
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from configuration import config


_embedding_model = None
_multilingual_embedding_model = None
logger = logging.getLogger(__name__)

LANGUAGE_ZH = "zh"
LANGUAGE_EN = "en"


def _connect():
    """打开一个 PostgreSQL 连接（dict 行工厂）。"""
    return psycopg.connect(config.POSTGRES_URI, row_factory=dict_row)


# --------- 建表 ---------

# entity_mapping：同义词 → 标准词映射表（PostgreSQL）。
# synonym 采用精确匹配（Postgres `=` 默认即大小写/字节精确，等价于原 MySQL utf8mb4_bin）。
sql_content = """
create table if not exists entity_mapping (
    id varchar(255) not null,
    synonym text not null,
    std_name text not null,
    entity_schema varchar(255) not null,
    is_reviewed integer not null default 0,
    create_time timestamptz not null default now(),
    update_time timestamptz,
    language varchar(8) not null default 'zh',
    normalized_synonym text,
    source varchar(64) not null default 'legacy',
    match_confidence double precision,
    -- synonym/std_name 可能是长文本（如 cause 描述，最长 >1200 字），故用 text；
    -- 唯一性走 md5(synonym) 生成列，避免 btree 对超长文本的 2704 字节索引上限。
    synonym_key text generated always as (md5(synonym)) stored,
    primary key (synonym_key, entity_schema)
);
"""

# `create table if not exists` 不会更新旧表。以下迁移全部是 ADD/ALTER/INDEX，
# 可在已有中文数据上重复执行，不删除或重写任何映射。
schema_migration_statements = (
    "alter table entity_mapping add column if not exists language varchar(8) not null default 'zh'",
    "alter table entity_mapping add column if not exists normalized_synonym text",
    "alter table entity_mapping add column if not exists source varchar(64) not null default 'legacy'",
    "alter table entity_mapping add column if not exists match_confidence double precision",
    "alter table entity_mapping add column if not exists synonym_key text generated always as (md5(synonym)) stored",
)


def _backfill_alias_metadata(cur):
    """Backfill legacy rows with the same normalization used by online lookup."""
    cur.execute(
        "select synonym, entity_schema, language, normalized_synonym, source "
        "from entity_mapping where normalized_synonym is null"
    )
    for row in cur.fetchall():
        language = row["language"]
        if row["source"] == "legacy":
            language = detect_alias_language(row["synonym"])
        cur.execute(
            "update entity_mapping set language=%s, normalized_synonym=%s "
            "where synonym=%s and entity_schema=%s",
            (
                language,
                normalize_alias(row["synonym"], language),
                row["synonym"],
                row["entity_schema"],
            ),
        )


def _migrate_synonym_key_primary_key(cur):
    """Move legacy `(synonym, entity_schema)` identity to the text-safe hash key."""
    cur.execute(
        """
        select con.conname,
               array_agg(att.attname order by key_column.ordinality) as columns
        from pg_constraint con
        cross join lateral unnest(con.conkey) with ordinality
            as key_column(attnum, ordinality)
        join pg_attribute att
          on att.attrelid=con.conrelid and att.attnum=key_column.attnum
        where con.conrelid='entity_mapping'::regclass and con.contype='p'
        group by con.conname
        """
    )
    primary_key = cur.fetchone()
    current_columns = list(primary_key["columns"]) if primary_key else []
    target_columns = ["synonym_key", "entity_schema"]
    if current_columns == target_columns:
        return
    if current_columns and current_columns != ["synonym", "entity_schema"]:
        raise RuntimeError(
            f"entity_mapping 存在无法自动迁移的主键列: {current_columns}"
        )

    cur.execute(
        "create unique index if not exists entity_mapping_synonym_key_schema_idx "
        "on entity_mapping (synonym_key, entity_schema)"
    )
    if primary_key:
        cur.execute(
            sql.SQL("alter table entity_mapping drop constraint {}").format(
                sql.Identifier(primary_key["conname"])
            )
        )
    cur.execute(
        "alter table entity_mapping add constraint entity_mapping_pkey "
        "primary key using index entity_mapping_synonym_key_schema_idx"
    )


def init_db():
    """创建/增量迁移映射表；幂等且不会删除已有中文数据。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
            # 只在旧安装仍为 varchar 时改为 text。新表已有依赖 synonym 的生成列，
            # 对同类型重复执行 ALTER TYPE 反而可能触发 PostgreSQL 依赖检查。
            for column_name in ("synonym", "std_name"):
                cur.execute(
                    "select data_type from information_schema.columns "
                    "where table_schema=current_schema() and table_name='entity_mapping' "
                    "and column_name=%s",
                    (column_name,),
                )
                column = cur.fetchone()
                if column and column["data_type"] != "text":
                    cur.execute(
                        f"alter table entity_mapping alter column {column_name} type text"
                    )
            for statement in schema_migration_statements:
                cur.execute(statement)
            _backfill_alias_metadata(cur)
            _migrate_synonym_key_primary_key(cur)
        conn.commit()


def detect_alias_language(text: str) -> str:
    """Phase 1 仅区分中文与英文术语；包含汉字的实体按中文处理。"""
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""):
        return LANGUAGE_ZH
    return LANGUAGE_EN


def normalize_alias(text: str, language: str | None = None) -> str:
    """生成用于确定性别名匹配的稳定形式，不改变存储/展示用原文。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if (language or detect_alias_language(normalized)) == LANGUAGE_EN:
        normalized = normalized.casefold()
    return normalized


def get_embedding_model():
    """获取嵌入模型"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(
            str(config.EMBEDDING_MODEL_PATH),
            device=config.resolve_device(),
        )
        print( "加载嵌入模型")
    return _embedding_model


def get_multilingual_embedding_model():
    """按需加载独立的多语种模型；不复用或替换现有中文 BGE 模型。"""
    global _multilingual_embedding_model
    model_path = config.MULTILINGUAL_EMBEDDING_MODEL_PATH
    if not model_path:
        raise RuntimeError("MULTILINGUAL_EMBEDDING_MODEL_PATH 未配置")
    if not model_path.exists():
        raise FileNotFoundError(f"多语种嵌入模型目录不存在: {model_path}")
    if _multilingual_embedding_model is None:
        _multilingual_embedding_model = SentenceTransformer(
            str(model_path),
            device=config.resolve_device(),
        )
        print("加载多语种嵌入模型")
    return _multilingual_embedding_model


def entity_alignment(datas, entity_schema, embed_batch_size=128):
    """
    实体对齐
    如果是初始化：
        向量化
        聚类
        选取高频词作为标准词
        所有同义词映射为标准词
    如果是增量更新：
        新实体向量化
        聚类
        选出新实体中的高频词作为临时标准词
        计算临时标准词和旧标准词的相似度
        如果临时标准词和旧标准词相似，使用旧标准词
        如果临时标准词没有相似项，将其作为新标准词
        所有同义词映射为标准词
    """
    field_type_mapping = {
        "name": "disease",
        "department": "department",
        "symptom": "symptom",
        "cause": "cause",
        "drug": "drug",
        "eat": "food",
        "not_eat": "food",
        "people": "people",
        "check": "check",
    }
    embedding_model = get_embedding_model()

    # 加载已对齐的同义词到标准词的映射
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, synonym, std_name from entity_mapping where entity_schema=%s and is_reviewed=1",
                (field_type_mapping[entity_schema],),
            )
            old_entity_mapping = cur.fetchall()
    old_entities = []
    if old_entity_mapping:
        print(
            
            f"读取 {len(old_entity_mapping)} 条已对齐的 {field_type_mapping[entity_schema]} 实体",
        )
        old_ids, old_entities, old_std_entities = zip(
            *[(x["id"], x["synonym"], x["std_name"]) for x in old_entity_mapping]
        )
        # 旧的实体ID
        old_ids = list(set(old_ids))
        # 旧的实体列表
        old_entities = list(set(old_entities))
        # 旧的标准词列表
        old_std_entities = list(set(old_std_entities))
        # 同义词到标准词的映射
        old_entity_mapping = {x["synonym"]: x["std_name"] for x in old_entity_mapping}

    # 收集所有新增实体，并统计出现频率
    new_entity_with_frequency = dict()
    for i in datas:
        entity = i.get(entity_schema)
        if not entity:
            continue
        if isinstance(entity, str):
            entity = [entity]
        for entity_item in entity:
            if not entity_item:
                continue
            frequency = new_entity_with_frequency.get(entity_item, 0) + 1  # 频率+1
            new_entity_with_frequency[entity_item] = frequency  # 更新频率

    # 取补集，筛选出新出现的实体
    new_entities = list(set(new_entity_with_frequency) - set(old_entities))

    # 如果有新增实体
    new_entity_mapping = {}  # 同义词 → 标准词
    if new_entities:
        print(
            
            f"检测到 {len(new_entities)} 个新增 {field_type_mapping[entity_schema]} 实体",
        )
        # 初始化与增量更新通用流程：将新实体聚类并根据频次选择标准词
        # 获取新实体的向量
        new_embeddings = embedding_model.encode(
            new_entities, batch_size=embed_batch_size, normalize_embeddings=True
        )
        # 使用 DBSCAN 聚类，相似的视为同义实体
        algorithm = DBSCAN(eps=0.15, min_samples=1, metric="cosine")
        # 得到每个实体对应的簇ID，长度和new_entities长度一致
        cluster_ids = algorithm.fit_predict(new_embeddings)
        # 将实体按簇编号组成列表
        cluster_dict = defaultdict(list)  # 簇ID → 实体列表
        for entity, cluster_id in zip(new_entities, cluster_ids):
            if cluster_id >= 0:  # 过滤噪声簇，理论上 min_samples=1 没有噪声簇，每个实体都有一个簇ID
                cluster_dict[cluster_id].append(entity)

        # 如果是初始化阶段，聚类，并选择高频词作为标准词
        if not old_entities:
            for cluster_id, entity_list in cluster_dict.items():
                # 选择每个簇中频率最高的概念作为标准词
                std_entity = max(
                    entity_list, key=lambda x: new_entity_with_frequency[x]
                )
                for entity in entity_list:
                    new_entity_mapping[entity] = std_entity
        else:
            temp_std_to_cluster: dict[str, list[str]] = {}  # 临时标准词 → 所有同义词
            for cluster_id, entity_list in cluster_dict.items():
                # 选择每个簇中频率最高的概念作为标准词
                std_entity = max(
                    entity_list, key=lambda x: new_entity_with_frequency[x]
                )
                temp_std_to_cluster[std_entity] = entity_list

            # 获取所有临时标准词的向量
            temp_std_list = list(temp_std_to_cluster.keys())
            temp_embeddings = embedding_model.encode(
                temp_std_list, batch_size=embed_batch_size, normalize_embeddings=True
            )
            # 获取旧标准词的向量(也可以先计算出id，再从向量数据库中获取，并对Mysql中有但是Chroma中没有的进行嵌入)
            old_embeddings = embedding_model.encode(
                old_std_entities, batch_size=embed_batch_size, normalize_embeddings=True
            )

            # 计算临时标准词与旧标准词的相似度
            similarity_matrix = cosine_similarity(temp_embeddings, old_embeddings)

            # 合并实体
            threshold = 0.85
            for i, temp_std in enumerate(temp_std_list):
                most_similar_idx = similarity_matrix[i].argmax()
                max_sim = similarity_matrix[i][most_similar_idx]
                # 如果临时标准词匹配到旧的标准词，将所有同义词映射到旧标准词
                if max_sim >= threshold:
                    for entity in temp_std_to_cluster[temp_std]:
                        new_entity_mapping[entity] = old_std_entities[most_similar_idx]
                # 如果临时标准词没有找到匹配，使用临时标准词作为新的标准词
                else:
                    for entity in temp_std_to_cluster[temp_std]:
                        new_entity_mapping[entity] = temp_std

        # 将新增实体的映射存储到 PostgreSQL
        insert_count = 0
        with _connect() as conn:
            with conn.cursor() as cur:
                for entity in new_entity_mapping:
                    cur.execute(
                        "insert into entity_mapping (id, synonym, std_name, entity_schema, is_reviewed) "
                        "values (%s, %s, %s, %s, 1) on conflict do nothing",
                        (
                            f"{field_type_mapping[entity_schema]}_{hashlib.md5(new_entity_mapping[entity].encode()).hexdigest()[:16]}",
                            entity,
                            new_entity_mapping[entity],
                            field_type_mapping[entity_schema],
                        ),
                    )
                    insert_count += cur.rowcount
                conn.commit()
                print(
                    f"添加 {insert_count} 条 {field_type_mapping[entity_schema]} 实体到数据库",
                )

    # 合并新旧标准词映射
    all_entity_mapping = new_entity_mapping
    if old_entity_mapping:
        all_entity_mapping.update(old_entity_mapping)

    # 替换原始数据
    for i in datas:
        entity = i.get(entity_schema)
        if not entity:
            continue
        if isinstance(entity, str):
            i[entity_schema] = all_entity_mapping.get(entity, entity)
        elif isinstance(entity, list):
            new_entity = []
            for entity_item in entity:
                new_entity.append(all_entity_mapping.get(entity_item, entity_item))
            i[entity_schema] = new_entity


def vector_indexing(datas, embed_batch_size=128, add_batch_size=256):
    """创建向量索引"""

    # 疾病:str,症状:list,诱因:str,药物:list,食物:list,人群类别:str,医学检查:list
    field_type_mapping = {
        "name": "disease",
        "department": "department",
        "symptom": "symptom",
        "cause": "cause",
        "drug": "drug",
        "eat": "food",
        "not_eat": "food",
        "people": "people",
        "check": "check",
    }

    vector_items = defaultdict(list)
    for data in datas:
        for key, value in data.items():
            field_type = field_type_mapping.get(key)
            if not field_type:
                continue
            if isinstance(value, str):
                if not value:
                    continue
                vector_items[field_type].append(
                    {
                        "id": f"{field_type}_{hashlib.md5(value.encode()).hexdigest()[:16]}",
                        "metadata": {"type": field_type},
                        "document": f"{value}",
                    }
                )
            elif isinstance(value, list):
                for i in value:
                    if not i:
                        continue
                    vector_items[field_type].append(
                        {
                            "id": f"{field_type}_{hashlib.md5(i.encode()).hexdigest()[:16]}",
                            "metadata": {"type": field_type},
                            "document": f"{i}",
                        }
                    )

    # 合并结果
    all_vector_items = (
        vector_items["disease"]
        + vector_items["department"]
        + vector_items["symptom"]
        + vector_items["cause"]
        + vector_items["drug"]
        + vector_items["food"]
        + vector_items["people"]
        + vector_items["check"]
    )
    ids = [x["id"] for x in all_vector_items]

    # 创建或加载向量数据库（显式使用余弦距离，与离线聚类/在线阈值语义一致）
    client = chromadb.PersistentClient(path=str(config.VECTOR_STORE_DIR))
    collection = client.get_or_create_collection(
        config.CHINESE_VECTOR_COLLECTION, metadata={"hnsw:space": "cosine"}
    )

    # 删数据库中与新增数据 ID 重复的数据，以及过滤新增数据中重复数据
    seen = set()
    old_ids = collection.get()["ids"]
    new_ids = set(ids) - set(old_ids)
    new_items = [
        (i["id"], i["metadata"], i["document"])
        for i in all_vector_items
        if i["id"] in new_ids
        and not (i["id"] in seen or seen.add(i["id"]))
        and i["document"]
    ]

    duplicate_data_num = len(set(ids)) - len(new_items)
    if duplicate_data_num:
        print( f"{duplicate_data_num} 条数据已存在于向量数据库中")
    if not new_items:
        return

    ids, metadatas, documents = zip(*new_items)
    ids = list(ids)
    documents = list(documents)
    metadatas = list(metadatas)
    # 批量嵌入
    embedding_model = get_embedding_model()
    embeddings = embedding_model.encode(
        documents,
        batch_size=embed_batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    # 批量添加到向量数据库
    for i in tqdm(range(0, len(ids), add_batch_size), desc="writing into chroma"):
        collection.add(
            ids=ids[i : i + add_batch_size],
            documents=documents[i : i + add_batch_size],
            metadatas=metadatas[i : i + add_batch_size],
            embeddings=embeddings[i : i + add_batch_size],
        )
    print( f"添加 {len(new_items)} 条数据到向量数据库")

    # 存储到 PostgreSQL
    insert_count = 0
    with _connect() as conn:
        with conn.cursor() as cur:
            for entity in new_items:
                cur.execute(
                    "insert into entity_mapping (id, synonym, std_name, entity_schema, is_reviewed) "
                    "values (%s, %s, %s, %s, 1) on conflict do nothing",
                    (
                        entity[0],  # id
                        entity[2],  # document
                        entity[2],  # document
                        entity[1]["type"],  # metadata[type]
                    ),
                )
                insert_count += cur.rowcount
            conn.commit()
            print( f"添加 {insert_count} 条实体到数据库")


class EntityAlignment:
    """实体对齐"""

    def __init__(self):
        self.chroma_client = chromadb.PersistentClient(path=str(config.VECTOR_STORE_DIR))

    def entity_mapping(self, text, entity_schema):
        """先做原有精确匹配，再做带语言元数据的规范化精确匹配。"""
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select std_name from entity_mapping where is_reviewed=1 and synonym=%s and entity_schema=%s",
                    (text, entity_schema),
                )
                row = cur.fetchone()
                if row:
                    return row["std_name"]

        language = detect_alias_language(text)
        normalized = normalize_alias(text, language)
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "select distinct std_name from entity_mapping "
                        "where is_reviewed=1 and normalized_synonym=%s "
                        "and entity_schema=%s and language=%s",
                        (normalized, entity_schema, language),
                    )
                    rows = cur.fetchall()
        except psycopg.errors.UndefinedColumn:
            # 允许应用在执行 Phase 1 增量迁移前继续使用原有中文精确/向量路径。
            logger.warning("entity_mapping 尚未执行多语种增量迁移，跳过规范化别名查询")
            return None

        canonical_names = {row["std_name"] for row in rows}
        if len(canonical_names) == 1:
            return canonical_names.pop()
        if len(canonical_names) > 1:
            logger.warning(
                "规范化别名存在歧义，拒绝自动对齐: text=%s schema=%s candidates=%s",
                text,
                entity_schema,
                sorted(canonical_names),
            )
        return None

    def vector_retrieve(self, text, where=None, n_results=1, threshold=None):
        """保留原有中文 BGE/Chroma 检索路径。"""
        if threshold is None:
            threshold = config.ENTITY_ALIGN_MAX_DISTANCE
        embedding = get_embedding_model().encode(text, normalize_embeddings=True)
        collection = self.chroma_client.get_collection(
            config.CHINESE_VECTOR_COLLECTION
        )
        res = collection.query(query_embeddings=embedding, n_results=n_results, where=where)
        # 按阈值过滤，返回标准词文本
        hits = [
            res["documents"][0][i]
            for i in range(len(res["ids"][0]))
            if res["distances"][0][i] < threshold
        ]
        return hits[0] if hits else None

    def multilingual_vector_retrieve(self, text, entity_schema):
        """在独立多语种索引中检索，返回其中记录的中文 canonical_name。"""
        if not config.MULTILINGUAL_EMBEDDING_MODEL_PATH:
            return None
        try:
            config.validate_vector_collection_isolation()
            collection = self.chroma_client.get_collection(
                config.MULTILINGUAL_VECTOR_COLLECTION
            )
            collection_count = collection.count()
            if not collection_count:
                return None
            embedding = get_multilingual_embedding_model().encode(
                text, normalize_embeddings=True
            )
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            result = collection.query(
                query_embeddings=[embedding],
                n_results=min(2, collection_count),
                where={"$and": [{"type": entity_schema}, {"reviewed": True}]},
            )
        except Exception:
            logger.exception("多语种实体向量检索不可用，英文实体按未匹配处理")
            return None

        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        candidates = []
        for distance, metadata in zip(distances, metadatas):
            canonical_name = (metadata or {}).get("canonical_name")
            if canonical_name:
                candidates.append((float(distance), canonical_name))
        if not candidates:
            return None

        best_distance, best_name = candidates[0]
        if best_distance >= config.MULTILINGUAL_ENTITY_ALIGN_MAX_DISTANCE:
            return None
        if len(candidates) > 1:
            second_distance, second_name = candidates[1]
            if (
                second_name != best_name
                and second_distance - best_distance
                < config.MULTILINGUAL_ENTITY_ALIGN_MIN_MARGIN
            ):
                logger.info(
                    "多语种实体候选过于接近，拒绝自动对齐: text=%s candidates=%s",
                    text,
                    candidates,
                )
                return None
        return best_name

    def _cache_vector_candidate(self, text, entity_schema, std_name, language):
        """缓存推断结果，但明确标记为未审核，不能成为后续确定性命中。"""
        entity_id = (
            f"{entity_schema}_{hashlib.md5(std_name.encode()).hexdigest()[:16]}"
        )
        normalized = normalize_alias(text, language)
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "insert into entity_mapping "
                        "(id, synonym, std_name, entity_schema, is_reviewed, language, "
                        "normalized_synonym, source) "
                        "values (%s, %s, %s, %s, 0, %s, %s, 'vector_candidate') "
                        "on conflict do nothing",
                        (
                            entity_id,
                            text,
                            std_name,
                            entity_schema,
                            language,
                            normalized,
                        ),
                    )
                conn.commit()
        except psycopg.errors.UndefinedColumn:
            # 旧表兼容：仍不把向量推断结果标为 reviewed。
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "insert into entity_mapping "
                        "(id, synonym, std_name, entity_schema, is_reviewed) "
                        "values (%s, %s, %s, %s, 0) on conflict do nothing",
                        (entity_id, text, std_name, entity_schema),
                    )
                conn.commit()

    def __call__(self, text, entity_schema):
        language = detect_alias_language(text)
        # 中英文都先使用经过审核的确定性别名映射。
        res = self.entity_mapping(text, entity_schema)
        if not res:
            if language == LANGUAGE_ZH:
                # 中文继续走原有 bge-base-zh-v1.5 索引，避免质量回退。
                res = self.vector_retrieve(text, where={"type": entity_schema})
            else:
                # 英文绝不送入中文模型；仅使用独立、显式配置的多语种索引。
                res = self.multilingual_vector_retrieve(text, entity_schema)
            if res:
                self._cache_vector_candidate(text, entity_schema, res, language)
        return res


if __name__ == "__main__":
    ea = EntityAlignment()
    print(ea("铁中毒", "disease"))
