// ai-clipper: preview a video link BEFORE downloading it (POST /api/probe):
// title, channel, duration, thumbnail, a quality picker with sizes, and "already imported".
import { useEffect, useState } from "react";
import { api, ApiError, type LinkPreview } from "../api";
import { fmtBytes, fmtTime } from "../format";
import { useI18n } from "../i18n";
import { href } from "../router";
import { Icon } from "./Icons";

export const looksLikeLink = (v: string) => /^https?:\/\/\S+\.\S+/i.test(v.trim());

export interface LinkPreviewState {
  preview: LinkPreview | null;
  probing: boolean;
  error: ApiError | null;
  height: number | null;
  setHeight: (h: number) => void;
  /** a link was typed and its preview is ready and not a duplicate */
  ready: boolean;
  /** extra fields for the create request (quality + real title) */
  body: { max_height?: number; title?: string };
}

export function useLinkPreview(url: string, enabled = true): LinkPreviewState {
  const [preview, setPreview] = useState<LinkPreview | null>(null);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const v = url.trim();
  const isLink = enabled && looksLikeLink(v);

  useEffect(() => {
    setPreview(null);
    setError(null);
    setHeight(null);
    if (!isLink) return;
    let stale = false;
    const timer = window.setTimeout(() => {
      setProbing(true);
      api
        .probe(v)
        .then((p) => {
          if (stale) return;
          setPreview(p);
          setHeight(p.default_height);
        })
        .catch((e) => !stale && setError(e instanceof ApiError ? e : new ApiError(String(e), null, 0)))
        .finally(() => !stale && setProbing(false));
    }, 600);
    return () => {
      stale = true;
      window.clearTimeout(timer);
      setProbing(false);
    };
  }, [v, isLink]);

  return {
    preview,
    probing,
    error,
    height,
    setHeight,
    ready: !isLink || (!!preview && !preview.existing_project && !probing),
    body: { ...(height ? { max_height: height } : {}), ...(preview ? { title: preview.title } : {}) },
  };
}

export function LinkPreviewPanel({ state, onOpenExisting }: { state: LinkPreviewState; onOpenExisting?: () => void }) {
  const { t } = useI18n();
  const { preview, probing, error, height, setHeight } = state;
  return (
    <>
      {probing && (
        <span className="field-hint link-preview-loading" role="status">
          <span className="spinner" /> {t("new.probe_loading")}
        </span>
      )}
      {error && (
        <div className="notice notice-danger" role="alert">
          <Icon name="alert" />
          <div>
            <div className="notice-title">{error.message}</div>
            {error.hint && <div className="muted">{error.hint}</div>}
          </div>
        </div>
      )}
      {preview && (
        <div className="link-preview">
          {preview.thumbnail && <img src={preview.thumbnail} alt="" referrerPolicy="no-referrer" />}
          <div className="link-preview-body">
            <strong dir="auto">{preview.title}</strong>
            <span className="muted">
              {[preview.uploader, preview.duration != null ? fmtTime(preview.duration) : null, preview.extractor]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {preview.existing_project ? (
              <span>
                {t("new.probe_duplicate")}{" "}
                <a href={href.project(preview.existing_project)} onClick={onOpenExisting}>
                  {t("new.probe_open")}
                </a>
              </span>
            ) : preview.qualities.length > 0 ? (
              <label className="link-preview-quality">
                <span className="muted">{t("new.quality")}</span>
                <select className="select" value={height ?? ""} onChange={(e) => setHeight(Number(e.target.value))}>
                  {preview.qualities.map((q) => (
                    <option key={q.height} value={q.height}>
                      {q.height}p{q.fps && q.fps > 30 ? Math.round(q.fps) : ""}
                      {q.size_bytes ? ` · ~${fmtBytes(q.size_bytes)}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}
