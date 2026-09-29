import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fmtCompact, fmtPercent, titleCase } from "../lib/format";
import { StatusBadge } from "../ui/badge";
import { Meter, Progress } from "../ui/misc";

describe("format helpers", () => {
  it("formats numbers", () => {
    expect(fmtCompact(1234)).toBe("1,234");
    expect(fmtCompact(1_500_000)).toMatch(/1\.5M/);
    expect(fmtPercent(0.1234)).toBe("12.3%");
    expect(titleCase("dead_letter")).toBe("Dead Letter");
  });
});

describe("status and meters", () => {
  it("never conveys status by colour alone", () => {
    render(<StatusBadge status="DEGRADED" />);
    expect(screen.getByText("Degraded")).toBeTruthy();
  });

  it("clamps progress and exposes aria values", () => {
    render(<Progress value={140} label="Sending" />);
    const bar = screen.getByRole("progressbar", { name: "Sending" });
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
  });

  it("shows 'no limit' for unlimited quotas", () => {
    render(<Meter label="Today" used={10} limit={null} />);
    expect(screen.getByText(/no limit/)).toBeTruthy();
  });
});
