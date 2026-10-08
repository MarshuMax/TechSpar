import { useState } from "react";
import { BookOpenCheck, ChevronDown, ChevronUp, FileText, ShieldAlert, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function PreparedAnswersView({ knowledge, loading = false, error = "" }) {
  const [expanded, setExpanded] = useState(null);
  if (loading) return <div className="rounded-2xl border border-border/70 p-5 text-sm text-dim">正在读取预编译问答...</div>;
  if (error) return <div className="rounded-2xl border border-red/20 bg-red/8 p-4 text-sm text-red">{error}</div>;
  if (!knowledge) return null;

  const stats = knowledge.compile_stats || {};
  const answers = Object.values(knowledge.prepared_answers || {});
  return (
    <Card className="border-primary/20 bg-gradient-to-b from-primary/4 to-transparent">
      <CardContent className="p-5 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/12 text-primary"><BookOpenCheck size={18} /></div>
            <div>
              <div className="font-semibold">Interview Knowledge Package</div>
              <div className="mt-0.5 text-xs text-dim">面试前生成，只读校对；修改资料后请重新准备。</div>
            </div>
          </div>
          <Badge variant={knowledge.index_status === "ready" ? "green" : "secondary"}>
            {knowledge.source_state === "stale" ? "Stale" : knowledge.index_status === "ready" ? "Ready" : knowledge.index_status || "Missing"}
          </Badge>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ["策略节点", stats.strategy_nodes || 0],
            ["预编译答案", stats.compiled_answers || 0],
            ["问题问法", stats.question_variants || 0],
            ["来源资料", stats.source_documents || 0],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border border-border/65 bg-card/70 px-3 py-2.5">
              <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
            </div>
          ))}
        </div>

        <div className="mt-4 space-y-2">
          {answers.map((answer) => {
            const open = expanded === answer.answer_id;
            return (
              <div key={answer.answer_id} className="rounded-2xl border border-border/70 bg-background/65">
                <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setExpanded(open ? null : answer.answer_id)}>
                  <Zap size={15} className="text-primary" />
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">{answer.topic || answer.node_id}</span>
                  <Badge variant="outline">{answer.question_variants?.length || 0} 问法</Badge>
                  {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </button>
                {open && (
                  <div className="space-y-4 border-t border-border/60 px-4 py-4">
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-dim">主答案</div>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-7">{answer.prepared_answer}</p>
                    </div>
                    {answer.short_answer && (
                      <div className="rounded-xl bg-primary/5 px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-wider text-primary/70">短答案</div>
                        <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6">{answer.short_answer}</p>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {(answer.question_variants || []).map((question) => <Badge key={question} variant="secondary">{question}</Badge>)}
                    </div>
                    {(answer.source_refs || []).length > 0 && (
                      <div className="space-y-1.5">
                        {(answer.source_refs || []).map((source, index) => (
                          <div key={`${source.document_id || source.source_type}-${index}`} className="flex items-start gap-2 text-xs text-dim">
                            <FileText size={13} className="mt-0.5 shrink-0" />
                            <span>{source.filename || source.source_type}{source.evidence ? `：${source.evidence}` : ""}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {(answer.warnings || []).map((warning) => (
                      <div key={warning} className="flex gap-2 rounded-xl border border-amber-500/20 bg-amber-500/8 px-3 py-2 text-xs text-amber-500">
                        <ShieldAlert size={13} /> {warning}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {answers.length === 0 && <div className="py-4 text-center text-sm text-dim">此 Prep 没有可用的预编译答案，实时阶段将使用 AI fallback。</div>}
        </div>
        {(knowledge.uncompiled_nodes || []).length > 0 && (
          <div className="mt-4 rounded-2xl border border-amber-500/20 bg-amber-500/8 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-amber-500"><ShieldAlert size={15} /> 未成功预编译的节点</div>
            <div className="mt-2 space-y-1.5">
              {knowledge.uncompiled_nodes.map((item) => (
                <div key={item.node_id} className="text-xs text-dim"><span className="font-medium text-text">{item.node_id}</span>：{item.error || "unknown"}</div>
              ))}
            </div>
          </div>
        )}
        {knowledge.index_error && <div className="mt-3 text-xs text-red">索引错误：{knowledge.index_error}</div>}
      </CardContent>
    </Card>
  );
}
