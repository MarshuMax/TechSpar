import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PreparedAnswersView from "./PreparedAnswersView";

describe("PreparedAnswersView", () => {
  it("shows read-only answers, variants and sources", () => {
    render(<PreparedAnswersView knowledge={{
      index_status: "ready",
      compile_stats: { strategy_nodes: 1, compiled_answers: 1, question_variants: 2, source_documents: 1 },
      prepared_answers: {
        pa1: {
          answer_id: "pa1",
          topic: "Shared Library",
          prepared_answer: "Main prepared answer",
          short_answer: "Short answer",
          question_variants: ["Why Shared Library?", "Why not Jenkinsfile?"],
          source_refs: [{ source_type: "personal_document", filename: "project.md", evidence: "pipeline reuse" }],
        },
      },
    }} />);
    fireEvent.click(screen.getByText("Shared Library"));
    expect(screen.getByText("Main prepared answer")).toBeInTheDocument();
    expect(screen.getByText("Why not Jenkinsfile?")).toBeInTheDocument();
    expect(screen.getByText(/project.md：pipeline reuse/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
