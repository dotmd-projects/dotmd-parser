# dotmd-parser

`.md` ファイルの依存グラフパーサー。`@include` / `@delegate` ディレクティブや `Read` 参照を解析し、ファイル間の依存関係をグラフとして構築します。

## インストール

```bash
pip install dotmd-parser
```

または開発用:

```bash
git clone https://github.com/dotmd-projects/dotmd-parser.git
cd dotmd-parser
pip install -e .
```

## 主要API

```python
from dotmd_parser import build_graph, resolve, dependents_of, summary
```

### build_graph — 依存グラフ構築

```python
graph = build_graph("./my-skill/")
# or
graph = build_graph("./my-skill/SKILL.md")
```

返り値:
```json
{
  "nodes": [{"id": "...", "type": "skill", "missing": false, "placeholders": []}],
  "edges": [{"from": "...", "to": "...", "type": "include", "parallel": false}],
  "warnings": []
}
```

### resolve — @include 展開

```python
result = resolve("./prompts/main.md", variables={"name": "Alice"})
print(result["content"])       # 展開後のテキスト
print(result["placeholders"])  # 未解決の {{変数}} リスト
```

### dependents_of — 逆依存クエリ

```python
# shared/role.md を変更したら影響を受けるファイル一覧
affected = dependents_of(graph, "/abs/path/to/shared/role.md")
```

### summary — 概要表示

```python
print(summary(graph))
# ノード数: 5  (agent:1, shared:2, skill:1, reference:1)
# エッジ数: 4  (include:3, read-ref:1)
# 警告数:  0
```

## ディレクティブ仕様

| ディレクティブ | 説明 |
|---|---|
| `@include path/to/file.md` | ファイルをインライン展開 |
| `@delegate path/to/agent.md` | エージェントに委譲（展開しない） |
| `@delegate path/to/agent.md --parallel` | 並列実行フラグ付き委譲 |
| `Read \`path/to/file.md\`` | ランタイム参照（展開しない、グラフには記録） |

## CLI

```bash
python -m dotmd_parser.parser ./my-skill/
```

## テスト

```bash
pip install pytest
pytest tests/ -v
```

## ライセンス

MIT
