import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ObligationCandidate } from "../api";
import { ObligationCardList } from "./ObligationCard";

const enDias = (dias: number) => new Date(Date.now() + dias * 86_400_000).toISOString().slice(0, 10);

const obligacion = (extra: Partial<ObligationCandidate>): ObligationCandidate =>
  ({
    obligation_id: "o1",
    title: "Modelo 130",
    description: "Pago fraccionado del IRPF.",
    risk_level: "high",
    confidence: 0.8,
    blocking_missing_facts: [],
    trigger_facts: [],
    evidence_refs: [
      {
        evidence_id: "ev1",
        title: "Art. 109 RIRPF",
        locator: "https://www.boe.es/buscar/act.php?id=BOE-A-2007-6820",
        section: "Artículo 109",
        snippet: null,
        source_type: "rule_catalog",
        document_type: null,
        hash: null,
        confidence: 0.9,
        retrieved_at: "2026-11-01T09:00:00Z",
        schema_version: "1.0",
      },
    ],
    due_date: enDias(5),
    due_date_note: null,
    status: "likely",
    jurisdiction: "ES",
    tax_period: "2026",
    schema_version: "1.0",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    ...extra,
  });

describe("ObligationCardList", () => {
  it("leads with the deadline and its urgency, the risk and the legal basis", () => {
    render(<ObligationCardList obligations={[obligacion({})]} />);
    const tarjeta = within(screen.getByRole("region", { name: "Obligaciones detectadas" })).getByRole("article", { name: "Modelo 130" });
    expect(tarjeta).toHaveTextContent(/quedan [45] días/);
    expect(tarjeta).toHaveTextContent("Riesgo alto");
    expect(tarjeta).toHaveTextContent("Art. 109 RIRPF");
    expect(tarjeta).toHaveTextContent("certeza estimada 80 %");
  });
});
