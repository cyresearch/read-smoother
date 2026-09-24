# Note templates

`book_note.md` and `paper_note.md` are the front matter + header written the first time Read Smoother
creates a note file. Placeholders (`{{title}}`, `{{author}}`, `{{pdf_path}}`, `{{date}}`;
for papers also `{{citekey}}`, `{{year}}`, `{{journal}}`, `{{url}}`, `{{zotero_uri}}`,
`{{authors_yaml}}`, `{{short_title}}`, `{{abstract_block}}`) are replaced; unknown ones become empty.

To customise without touching the tracked files, copy one to `*.local.md` (git-ignored) and point
`book_note_template` / `paper_note_template` in `config.json` at it. Read Smoother appends its own
"Highlights & notes" section after whatever the template produces.
