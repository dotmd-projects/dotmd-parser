You extract a formal domain ontology from a single analysis document.

Read the document below and identify ontology elements it states or implies:
classes (entities), datatype properties, object properties (typed relations
between classes), controlled vocabularies (enums), invariants (identities /
business rules explicitly written), conflicts (contradictions the text itself
notes), and open questions.

## Rules
- Only extract what the document supports. Do NOT invent classes or relations.
- Class / property `name`: English PascalCase (class) / camelCase (property).
- `label_ja`: the Japanese term as written.
- datatype `type` ∈ {string, decimal, integer, date, dateTime, boolean}.
- object property `cardinality`: one of `1:1`, `多:1`, `1:多`, `1:0..1`, `1:0..*`, `多:0..1`, `多:多`.
- `characteristics` ⊆ {Functional, InverseFunctional, Symmetric, Transitive}.
- `enum` on a datatype property = the `name` of a vocabulary in this same output, else null.
- Do NOT output provenance — the caller attaches the source path.

## Document
### {{doc_path}}
```
{{doc_content}}
```

## Output format (JSON)
Return **only** this JSON, no prose outside the block. Empty arrays are fine.

```json
{
  "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
  "datatype_properties": [{"name": "feeRate", "domain": "Application", "type": "decimal", "label_ja": "手数料率", "enum": null}],
  "object_properties": [{"name": "evaluatedBy", "from": "Application", "to": "CreditAssessment", "cardinality": "1:1", "characteristics": ["Functional"], "note": "鎖"}],
  "vocabularies": [{"name": "applicationStatus", "values": ["買取成立", "謝絶"]}],
  "invariants": [{"id": "roas-identity", "statement": "ROAS = 申請件数単価 ÷ 申請CPA", "kind": "identity"}],
  "conflicts": [{"kind": "contradiction", "detail": "審査CV(681) < 申請CV(1290)"}],
  "open_questions": [{"text": "審査CVの正確な定義"}]
}
```
