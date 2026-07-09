from configuration import config
import hashlib
import chromadb
import psycopg
from psycopg.rows import dict_row
from tqdm import tqdm
from sklearn.cluster import DBSCAN
from collections import defaultdict
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


_embedding_model = None


def _connect():
    """打开一个 PostgreSQL 连接（dict 行工厂）。"""
    return psycopg.connect(config.POSTGRES_URI, row_factory=dict_row)


# --------- 建表 ---------

# entity_mapping：同义词 → 标准词映射表（PostgreSQL）。
# synonym 采用精确匹配（Postgres `=` 默认即大小写/字节精确，等价于原 MySQL utf8mb4_bin）。
sql_content = """
create table if not exists entity_mapping (
    id varchar(255) not null,
    synonym varchar(255) not null,
    std_name varchar(255) not null,
    entity_schema varchar(255) not null,
    is_reviewed integer not null default 0,
    create_time timestamptz not null default now(),
    update_time timestamptz,
    primary key (synonym, entity_schema)
);
"""


def init_db():
    """建表。仅离线管道显式调用，不在 import 时执行副作用。数据库本身由 docker-compose 自动创建。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
        conn.commit()


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
                        "values (%s, %s, %s, %s, 1) on conflict (synonym, entity_schema) do nothing",
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
        "smart_medical", metadata={"hnsw:space": "cosine"}
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
                    "values (%s, %s, %s, %s, 1) on conflict (synonym, entity_schema) do nothing",
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
        self.embedding_model = get_embedding_model()
        self.chroma_client = chromadb.PersistentClient(path=str(config.VECTOR_STORE_DIR))

    def entity_mapping(self, text, entity_schema):
        """标准词映射"""
        res = None
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select std_name from entity_mapping where is_reviewed=1 and synonym=%s and entity_schema=%s",
                    (text, entity_schema),
                )
                row = cur.fetchone()
                if row:
                    res = row["std_name"]
        return res

    def vector_retrieve(self, text, where=None, n_results=1, threshold=None):
        """向量检索（余弦距离 distance = 1 - cos_sim，越小越相似；超过阈值判为不匹配）"""
        if threshold is None:
            threshold = config.ENTITY_ALIGN_MAX_DISTANCE
        embedding = self.embedding_model.encode(text, normalize_embeddings=True)
        collection = self.chroma_client.get_collection("smart_medical")
        res = collection.query(query_embeddings=embedding, n_results=n_results, where=where)
        # 按阈值过滤，返回标准词文本
        hits = [
            res["documents"][0][i]
            for i in range(len(res["ids"][0]))
            if res["distances"][0][i] < threshold
        ]
        return hits[0] if hits else None

    def __call__(self, text, entity_schema):
        # 先从同义词-标准词中匹配
        res = self.entity_mapping(text, entity_schema)
        # 如果没有匹配成功，嵌入并检索（vector_retrieve 已按阈值过滤，不匹配返回 None）
        if not res:
            res = self.vector_retrieve(text, where={"type": entity_schema})
            if res:
                # 将文本和检索出来的标准词写入缓存（补齐 NOT NULL 的 id 列）
                entity_id = f"{entity_schema}_{hashlib.md5(res.encode()).hexdigest()[:16]}"
                with _connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "insert into entity_mapping (id, synonym, std_name, entity_schema, is_reviewed) "
                            "values (%s, %s, %s, %s, 1) on conflict (synonym, entity_schema) do nothing",
                            (entity_id, text, res, entity_schema),
                        )
                    conn.commit()
        return res


if __name__ == "__main__":
    ea = EntityAlignment()
    print(ea("铁中毒", "disease"))
