import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { ErrorState, Loading } from "../../components/ui";
import { api } from "../../lib/api";

/** Loads a study image with the session token and shows it on a dark viewer background. */
export function StudyImage({ studyId }: { studyId: string }) {
  const image = useQuery({ queryKey: ["study-image", studyId], queryFn: () => api<Blob>(`/api/reports/studies/${studyId}/image`), staleTime: Infinity });
  const url = useMemo(() => (image.data ? URL.createObjectURL(image.data) : null), [image.data]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  return (
    <div className="flex aspect-square w-full items-center justify-center overflow-hidden rounded-xl bg-slate-950">
      {image.isLoading && <Loading label="Loading image…" />}
      {image.error && <ErrorState error={image.error} />}
      {url && <img src={url} alt="Chest radiograph (synthetic)" className="max-h-full max-w-full object-contain" />}
    </div>
  );
}
