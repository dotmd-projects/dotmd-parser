You are an expert in analyzing dependencies between documents.

Analyze the documents listed below and detect dependencies between them.

## Criteria

- A document depends on another when it presupposes its content (citations, references, "see X", etc.).
- A document that defines shared concepts or terminology is a dependency of documents that use them.
- Dependencies flow from higher-level concepts (policy, design) down to lower-level concepts (implementation, procedure).
- If multiple documents reference the same rule or definition, propose a shared part (`shared/...`).

Propose common elements that should be extracted as shared parts when appropriate.

## Document list

{{file_list}}

## Output format (JSON)

Return **only** the following JSON. Do not add prose, markdown, or explanation outside the JSON block.

Write the `reason` field in the **same language as the source documents** (e.g. Japanese reasons for Japanese docs, English reasons for English docs).

```json
{
  "edges": [
    {
      "from": "relative path of the depending file",
      "to": "relative path of the depended-on file",
      "reason": "one-sentence justification, matching the source language"
    }
  ],
  "shared_proposals": [
    {
      "name": "proposed shared-part filename (e.g. shared/xxx.md)",
      "content_summary": "summary of the shared part",
      "used_by": ["list of files that should use this shared part"],
      "reason": "one-sentence justification, matching the source language"
    }
  ]
}
```
