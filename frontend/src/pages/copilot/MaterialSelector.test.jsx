import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MaterialSelector from "./MaterialSelector";
import * as personalAgentApi from "../../api/personalAgent";

vi.mock("../../api/personalAgent", () => ({
  getDocuments: vi.fn(),
  uploadDocument: vi.fn(),
}));

describe("MaterialSelector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("only selects ready documents", async () => {
    personalAgentApi.getDocuments.mockResolvedValue({
      items: [
        { document_id: "ready", filename: "project.md", status: "ready", size_bytes: 100, chunk_count: 2 },
        { document_id: "error", filename: "broken.md", status: "error", size_bytes: 100, chunk_count: 0 },
      ],
      supported_extensions: [".md"],
      max_upload_bytes: 1024,
    });
    const onChange = vi.fn();
    render(<MaterialSelector value={[]} onChange={onChange} />);

    await screen.findByText("project.md");
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    expect(onChange).toHaveBeenCalledWith(["ready"]);
    expect(checkboxes[1]).toBeDisabled();
  });

  it("selects a newly uploaded ready document", async () => {
    personalAgentApi.getDocuments.mockResolvedValue({ items: [], supported_extensions: [".md"], max_upload_bytes: 1024 });
    personalAgentApi.uploadDocument.mockResolvedValue({
      document_id: "uploaded", filename: "script.md", status: "ready", size_bytes: 20, chunk_count: 1,
    });
    const onChange = vi.fn();
    const { container } = render(<MaterialSelector value={[]} onChange={onChange} />);
    await screen.findByText(/暂无个人资料/);
    const input = container.querySelector('input[type="file"]');
    fireEvent.change(input, { target: { files: [new File(["content"], "script.md", { type: "text/markdown" })] } });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(["uploaded"]));
  });
});
