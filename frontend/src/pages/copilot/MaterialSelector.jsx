import { useEffect, useRef, useState } from "react";
import { FilePlus2, FileText, Loader2, RefreshCw } from "lucide-react";

import { getDocuments, uploadDocument } from "../../api/personalAgent";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatFileSize } from "./shared";

export default function MaterialSelector({ value = [], onChange, roles = {}, onRolesChange = () => {}, disabled = false }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [accept, setAccept] = useState("");
  const fileRef = useRef(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getDocuments();
      setDocuments(data.items || []);
      setAccept((data.supported_extensions || []).join(","));
    } catch (err) {
      setError(err.message || "资料加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = (documentId) => {
    if (disabled) return;
    if (value.includes(documentId)) {
      onChange(value.filter((item) => item !== documentId));
    } else {
      onChange([...value, documentId]);
      onRolesChange({ ...roles, [documentId]: roles[documentId] || "supporting_material" });
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const document = await uploadDocument(file);
      setDocuments((items) => [document, ...items.filter((item) => item.document_id !== document.document_id)]);
      if (document.status === "ready" && !value.includes(document.document_id)) {
        onChange([...value, document.document_id]);
        onRolesChange({ ...roles, [document.document_id]: "supporting_material" });
      }
      else setError(document.error || "资料尚未完成索引，暂不能选择");
    } catch (err) {
      setError(err.message || "资料上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="rounded-[28px] border border-border/80 bg-background/65 p-4 md:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-dim/80">面试资料</div>
          <div className="mt-1 text-sm text-dim">只会使用本次勾选的个人资料预编译回答。</div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={value.length ? "purple" : "secondary"}>{value.length} 已选择</Badge>
          {!disabled && (
            <>
              <input
                ref={fileRef}
                type="file"
                accept={accept}
                className="hidden"
                onChange={(event) => handleUpload(event.target.files?.[0])}
              />
              <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
                {uploading ? <Loader2 className="animate-spin" /> : <FilePlus2 />} 上传新资料
              </Button>
            </>
          )}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-dim"><Loader2 className="animate-spin" /> 正在加载资料库...</div>
      ) : documents.length === 0 ? (
        <div className="py-6 text-sm text-dim">暂无个人资料。可以上传项目讲稿、复盘笔记或技术准备材料。</div>
      ) : (
        <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
          {documents.map((document) => {
            const ready = document.status === "ready";
            const checked = value.includes(document.document_id);
            return (
              <label
                key={document.document_id}
                className={`flex items-center gap-3 rounded-2xl border px-3.5 py-3 transition-colors ${
                  ready && !disabled ? "cursor-pointer hover:bg-hover/60" : "cursor-not-allowed opacity-65"
                } ${checked ? "border-primary/35 bg-primary/7" : "border-border/70 bg-card/60"}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled || !ready}
                  onChange={() => toggle(document.document_id)}
                  className="h-4 w-4 accent-primary"
                  aria-label={`选择 ${document.filename}`}
                />
                <FileText size={17} className={checked ? "text-primary" : "text-dim"} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{document.filename}</div>
                  <div className="mt-0.5 text-xs text-dim">
                    {formatFileSize(document.size_bytes)} · {ready ? `${document.chunk_count} 个资料片段` : document.status}
                  </div>
                </div>
                {checked && ready && (
                  <select
                    value={roles[document.document_id] || "supporting_material"}
                    disabled={disabled}
                  onChange={(event) => onRolesChange({ ...roles, [document.document_id]: event.target.value })}
                    onClick={(event) => event.stopPropagation()}
                    className="rounded-lg border border-border/70 bg-background px-2 py-1 text-xs"
                    aria-label={`${document.filename} 资料角色`}
                  >
                    <option value="authoritative_script">权威讲稿</option>
                    <option value="supporting_material">辅助资料</option>
                  </select>
                )}
                {!ready && <Badge variant="secondary">不可用</Badge>}
              </label>
            );
          })}
        </div>
      )}

      {error && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-red/20 bg-red/8 px-3 py-2 text-xs text-red">
          <span>{error}</span>
          <button onClick={load} className="inline-flex items-center gap-1 hover:underline"><RefreshCw size={12} />重试</button>
        </div>
      )}
    </div>
  );
}
