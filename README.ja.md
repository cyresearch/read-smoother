[English](README.md) | [中文](README.zh.md) | 日本語

# Read Smoother（畅读）

**なめらかに読む。聞きながら読む。** Read Smoother はローカルで動く小さなウェブアプリです。Microsoft のニューラル音声が PDF を一文ずつ読み上げ、読んでいる文がページ上でハイライトされていきます。単語にマウスを乗せれば辞書の説明が出ます。気に入った一文はキー一つでノートへ。論文は Zotero のライブラリから直接開けます。分からないところは、そのページの横で自分の AI に聞けます。AI は今どの文を聞いているかを知っています。

中国語名は「畅读（chàngdú）」、なめらかに読む、という意味です。

![Read Smoother の閲読ページ](docs/screenshots/reading-en.png)

## 🌱 Read Smoother が生まれた理由

Read Smoother はとても具体的な困りごとから生まれました。英語の学術書や論文を目だけで読むのは遅い、ということです。情報の密なページでは注意が滑り、筋を見失います。聞きながら読むと楽になりますが、これは一人の読者の習慣にとどまりません。第二言語の読解研究では、音声を聞きながら読むほうが黙読より理解と読む速さが上がり（Chang & Millett, 2015）、聞くだけより理解が進み（Chang, 2009）、新しい語彙は読みと聞きが同時にあるほうが定着します（Brown, Waring & Donkaewbua, 2008; Webb & Chang, 2012）。

スクリーンリーダーやオーディオブックのアプリは声を与えてくれますが、読書に必要な残りのもの、つまり目の前の本文、辞書、メモを書く場所、段落がほどけないときに聞ける相手は与えてくれません。Read Smoother はそれらを一つのページにまとめ、研究者がすでに使っているツール（Obsidian と Zotero）につなぎ、読者が本のどこにいるかを本当に知っている AI を横に置きます。

## ✨ できること

- **テキスト付きの PDF なら何でも読み上げ**。Microsoft のニューラル音声（英語 47 声、他の言語も設定可）、速度 0.6 倍から 2 倍。読んでいる文がページ上でハイライトされ、ページは自動でめくれます。ページをまたぐ文も一つの文として読みます。
- **目次サイドバー**。PDF のしおりを使い、しおりがなければフォントの大きさと太さから見出しを推定します（学術論文でよく効きます）。見出しをクリックすると音声がそこへ飛びます。
- **単語にホバーで辞書**。半秒止めると macOS 内蔵辞書の発音と語義がカードで出ます（Oxford、有効にしていれば英中 Oxford も）。句は句として扱います。in spite of の spite に乗せれば句全体の説明、drop important people in の drop に乗せれば句動詞を提示します。
- **キー一つでハイライトとメモ**。ハイライトは Markdown ノートの引用ブロックになり、その下にコメントを書けます。Markdown ファイルが唯一の原本で、Obsidian で直せばページに反映されます。
- **Zotero**。著者・年・タイトル・citekey でライブラリを検索し、PDF を開くと、ハイライトはその論文の `@citekey.md` に入ります。Zotero の API は不要で、たぶんもう使っている Better BibTeX の自動エクスポートだけで動きます。
- **自分の AI に聞く**。ページ横のパネルで、ローカルの [Claude Code](https://claude.com/claude-code) コマンドラインを通して動くので、既存のサブスクリプションが使え、モデル（Haiku、Sonnet、Opus、Fable）はプルダウンで選べます。今聞いている文とその前後を常に知っていて、本全体を検索してページ番号を示し、あなたのノートや指定したローカルファイルを読み、あなたが書いた短いプロフィールを読んで誰と話しているかを理解します。回答はワンクリックで現在の文のメモに保存できます。
- **中国語・英語の UI** をボタン一つで切り替え。

## 🧠 どこを読んでいるか知っている AI

「PDF とチャット」系のツールの多くは、文書を入れて質問を打つだけです。ここではアシスタントが音声の隣にいます。

- 今聞いている文、その前後二文、現在のページが、質問のたびに一緒に送られます。「この文はどういう意味？」と聞けば、*その*文について答えます。クイック質問ボタン（と `1` キー）なら打たずに送れます。
- 本全体（ページ番号付きテキストに書き出したもの）を読み取り専用で検索できるので、「この用語はどこで導入された？」に推測ではなくページ番号が返ります。
- `ai_read_dirs` に挙げたフォルダと、質問に貼ったパスを読めます。「これを Smith 2019 の私のメモと比べて」と言ったり、ファイルを入力欄にドロップしたりできます。
- `profile.md` はあなたが書くものです。何者か、何を研究しているか、どう説明されたいか。システムプロンプトに差し込まれ、そのプロンプトの一部としてモデルに送られる以外にマシンの外へは出ません。
- 会話は本ごとに保存され、セッションをまたいで記憶されます。「新しい会話」で記憶を消せます。

## 🗂 Obsidian と Zotero

Read Smoother が書くのは普通の Markdown です。`vault_path` を Obsidian の vault に向けると、本は `notes_folder` に、論文は `paper_notes_folder` に `@citekey.md` としてノートができます。ノートがすでにある場合（Obsidian の Citations プラグインが作ったものなど）、Read Smoother は末尾に「Highlights & notes」セクションを追加するだけで、ファイルの他の部分には触れません。セクション内の一項目はこうなります。

```markdown
%% hl:3f9a2c1e %%
> [!quote] p.42 [🆉](zotero://open-pdf/library/items/ABCD1234?page=42)
> ハイライトした文。

あなたのコメント。ここで直してもページで直しても構いません。
```

`%% ... %%` の行は Obsidian の閲覧ビューでは見えず、Read Smoother が項目を識別するためのものです。論文の引用には `zotero://open-pdf` リンクが付き、クリックすると Zotero でそのページが開きます。新規ノートの冒頭は `templates/book_note.md` と `templates/paper_note.md` から作られ、自分のものに差し替えられます（`templates/README.md` 参照）。

Zotero については、Better BibTeX が自動エクスポートする `.bib` ファイル（各文献の PDF パスを含む）を読むので、Zotero が起動していなくても動きます。

## 🚀 はじめかた

必要なもの: macOS（辞書とデスクトップランチャーがシステム機能を使います。それ以外は素の Python なので他の OS でも辞書なしで動くはずですが、macOS でしかテストされていません）、Python 3.10 以上、音声のためのインターネット接続、アプリ風ウィンドウが欲しければ Google Chrome、AI パネルが欲しければ [Claude Code](https://claude.com/claude-code)。

```bash
git clone https://github.com/cyresearch/read-smoother.git
cd read-smoother
cp config.example.json config.json     # パスを書き換える
cp profile.example.md profile.md       # 任意: AI に自己紹介する
./start.sh                             # .venv を作り依存を入れ http://127.0.0.1:8765/ を開く
```

自分のファイルに向ける前に、同梱のパブリックドメインの本で試せます。

```bash
./start.sh demo/The-Elements-of-Style.pdf --title "The Elements of Style" --author "William Strunk Jr."
```

自分の本は、PDF を `books/` に入れる（本棚に現れます）か、`./start.sh path/to/book.pdf --title "..." --author "..."` で追加します。

ターミナルなしでダブルクリックで開く、Chrome ウィンドウ付きのアプリを作るには:

```bash
osacompile -o ~/Desktop/"Read Smoother.app" app/read-smoother.applescript
cp app/icon.icns ~/Desktop/"Read Smoother.app"/Contents/Resources/applet.icns
```

リポジトリを `~/projects/read-smoother` 以外に置いた場合は、先にスクリプトの `proj` の行を直してください。本棚ページの「Quit」ボタンでバックグラウンドのサーバーを止められます。

## ⚙️ 設定

すべて `config.json` にあります（`config.example.json` からコピー）。相対パスはプロジェクトフォルダ基準、`~` は展開されます。

| キー | 意味 |
|---|---|
| `language` | `en` か `zh`。UI の既定言語と、ノートに書き込む見出しの言語。UI はブラウザごとに切り替えられます。 |
| `vault_path` | ノートを受け取るフォルダ。通常は Obsidian の vault。空なら `./notes`。 |
| `vault_name` | vault の名前。`obsidian://` リンクに使います。空なら Obsidian ボタンは出ません。 |
| `notes_folder`, `paper_notes_folder` | 本のノートと論文のノートのサブフォルダ。 |
| `book_note_template`, `paper_note_template` | 新規ノート冒頭の Markdown テンプレート。 |
| `bib_path` | Zotero ライブラリの Better BibTeX 自動エクスポート。空なら Zotero 検索は無効。 |
| `default_voice`, `tts_locales` | 既定の声と、声のメニューに出すロケール（例 `["en", "ja"]`）。 |
| `ai_read_dirs` | AI が読めるフォルダ。読み取り専用、既定は空。 |
| `profile_path` | AI に渡すプロフィール。 |

実行時フラグ: `--port`、`--config other.json`、`--data-dir somewhere`（進捗、ハイライトの位置、会話の保存先。既定は `./data`）、`--no-browser`。

## 🔒 マシンの外に出るもの

- **再生した文**は [edge-tts](https://github.com/rany2/edge-tts) を通じて Microsoft の音声合成エンドポイントに送られます。Edge ブラウザの読み上げ機能と同じサービスで、公式 API ではありません。音声はローカルの `cache/` にキャッシュされます。
- **AI に質問したとき**: 質問、現在のページの本文、文の前後、あなたの `profile.md`、AI がツールで読んだ内容が、Claude Code を通じて Anthropic に送られます。これらの呼び出しでは外部コネクタ（MCP サーバー）を無効にしており、AI が持つのは読み取り専用のファイルツールだけです。
- 辞書はオフライン。Zotero 検索はローカルファイルを読むだけ。ノートはローカルの Markdown。テレメトリはありません。

## ⌨️ キー

| | |
|---|---|
| `Space` | 再生 / 一時停止 |
| `←` `→` | 前の文 / 次の文 |
| `[` `]` | 前のページ / 次のページ（末尾を越えてスクロールしてもめくれます） |
| `H` | 現在の文をハイライト（もう一度で解除） |
| `N` | 現在の文にメモを書く |
| `Shift` + クリック | 現在の文からクリックした文までをハイライト |
| `-` `=` | 遅く / 速く |
| `T` `D` `A` | 目次、辞書のオンオフ、AI 入力欄にフォーカス |
| `1` から `9` | クイック質問 |

## 🔄 まだ育てている途中です

Read Smoother は開発中です。作者自身が毎日の読書に使い、その体験に合わせて調整し続けているので、粗いところや変更があります。Issue と PR を歓迎します。特に、文の分割や見出し検出がうまくいかない PDF の報告はありがたいです。

## 📚 参考文献

Brown, R., Waring, R., & Donkaewbua, S. (2008). Incidental vocabulary acquisition from reading, reading-while-listening, and listening to stories. *Reading in a Foreign Language, 20*(2), 136–163.

Chang, A. C.-S. (2009). Gains to L2 listeners from reading while listening vs. listening only in comprehending short stories. *System, 37*(4), 652–663. https://doi.org/10.1016/j.system.2009.09.009

Chang, A. C.-S., & Millett, S. (2015). Improving reading rates and comprehension through audio-assisted extensive reading for beginner learners. *System, 52*, 91–102. https://doi.org/10.1016/j.system.2015.05.003

Webb, S., & Chang, A. C.-S. (2012). Vocabulary learning through assisted and unassisted repeated reading. *The Canadian Modern Language Review, 68*(3), 267–290. https://doi.org/10.3138/cmlr.1204.1

## ライセンス

[AGPL-3.0](LICENSE)。Read Smoother は [PyMuPDF](https://pymupdf.readthedocs.io/)（AGPL-3.0）に依存しているため、プロジェクトも同じライセンスです。他の依存: [edge-tts](https://github.com/rany2/edge-tts)（LGPL-3.0）、[PyObjC](https://pyobjc.readthedocs.io/)（MIT）、ブラウザ側では cdnjs から読み込む [PDF.js](https://mozilla.github.io/pdf.js/)（Apache-2.0）、[marked](https://marked.js.org/)（MIT）、[DOMPurify](https://github.com/cure53/DOMPurify)（Apache-2.0 / MPL-2.0）。デモの本は Project Gutenberg の *The Elements of Style*（1918）、パブリックドメインです。
