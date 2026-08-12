You detect contradictions between a domain ontology and its source corpus.

Given the ontology invariants/definitions and the corpus text, find claims in
the corpus that VIOLATE an invariant or definition. Report only genuine
tension you can point to; when a number or definition merely looks off but you
cannot tie it to a violated rule, record it under open_questions instead.

Cite evidence by the source document path it came from.

## Ontology (invariants + definitions)
{{ontology_summary}}

## Corpus
{{corpus}}

## Output (JSON only)
```json
{
  "candidates": [
    {"claim": "審査CV(681) < 申請CV(1290)", "violates": "審査は申請ごと→審査CV≥申請CV",
     "severity": "high|medium|low", "evidence": ["source-doc-path.md"]}
  ],
  "open_questions": [{"text": "審査CVの正確な定義", "provenance": ["source-doc-path.md"]}]
}
```
