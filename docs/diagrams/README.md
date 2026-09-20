# Architecture diagrams

All diagrams share one canvas (1600 × 1200) and one style, and come in two versions:

| Diagram | Overview (titles only) | Detailed (annotated) |
| --- | --- | --- |
| Camera preprocessing → mini-DB → query | `preprocessing-schema.svg` / `.png` | `preprocessing-schema-detailed.svg` / `.png` |
| Event → camera → frame → frontend routing | `query-routing-schema.svg` / `.png` | `query-routing-schema-detailed.svg` / `.png` |
| Frontend: one Footage object, three renderers | `frontend-schema.svg` / `.png` | `frontend-schema-detailed.svg` / `.png` |

Both versions are generated from the same layout in `gen_diagrams.py`; edit the
content there, then:

```sh
uv run python docs/diagrams/gen_diagrams.py
for f in docs/diagrams/*.svg; do
  google-chrome --headless --hide-scrollbars --window-size=1600,1200 \
    --screenshot="${f%.svg}.png" "file://$PWD/$f"
done
```
