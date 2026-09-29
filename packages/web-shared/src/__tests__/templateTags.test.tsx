import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TEMPLATE_TAGS, applySampleTags } from "../lib/templateTags";
import { TemplateTagsTable } from "../ui/templateTags";

describe("template tags", () => {
  it("lists every supported tag", () => {
    expect(TEMPLATE_TAGS.map((t) => t.tag)).toEqual([
      "#USERID#", "#RANDOM#", "#EMAIL#", "#SUBSID#", "#INVOICE#", "#REF#", "#HASH#", "#DATE#", "#TIME#", "#OTP#", "#$$#", "#MASSAGE#",
    ]);
  });

  it("renders sample values for previews, HTML-escaped", () => {
    const out = applySampleTags("Hi #USERID# <#EMAIL#> #MASSAGE# #UNKNOWN#", { email: "a&b@x.org", messages: ["<b>Welcome</b>"], mode: "html" });
    // Only the inserted values are escaped; the template's own markup and unknown tags stay as written.
    expect(out).toBe("Hi a&amp;b <a&amp;b@x.org> &lt;b&gt;Welcome&lt;/b&gt; #UNKNOWN#");
    expect(applySampleTags("#OTP# #$$# #DATE#")).toMatch(/^583921 47 \d{4}-\d{2}-\d{2}$/);
  });

  it("copies a tag and offers insert", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const onInsert = vi.fn();
    render(<TemplateTagsTable onInsert={onInsert} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy #INVOICE#" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("#INVOICE#"));
    fireEvent.click(screen.getByRole("button", { name: "Insert #REF#" }));
    expect(onInsert).toHaveBeenCalledWith("#REF#");
    expect(screen.getByText("Example output")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy all tags" }));
    await waitFor(() => expect(writeText.mock.calls.at(-1)?.[0]).toContain("#MASSAGE#"));
  });
});
