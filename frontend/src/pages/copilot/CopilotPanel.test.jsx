import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import CopilotPanel from "./CopilotPanel";

describe("CopilotPanel", () => {
  it("labels compiled and fallback answers distinctly", () => {
    const { rerender } = render(<CopilotPanel
      update={{ intent: "project", topic: "Project", confidence: 0.9 }}
      streamingAnswer="Prepared response"
      answerLoading={false}
      answerStreaming={false}
      answerMeta={{ source: "prepared", confidence: 0.92, latencyMs: 73, shortAnswer: "Short", utteranceId: "u1" }}
    />);
    expect(screen.getByText("预编译参考答案")).toBeInTheDocument();
    expect(screen.getByText(/Prepared · 92% · 73ms/)).toBeInTheDocument();

    rerender(<CopilotPanel
      update={{ intent: "technical", topic: "CFS", confidence: 0.4 }}
      streamingAnswer="Generated response"
      answerLoading={false}
      answerStreaming={false}
      answerMeta={{ source: "llm_fallback", utteranceId: "u2" }}
    />);
    expect(screen.getByText("AI 实时参考答案")).toBeInTheDocument();
  });
});
