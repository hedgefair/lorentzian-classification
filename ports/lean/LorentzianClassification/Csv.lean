/-!
# CSV reader/writer for TradingView parity baselines

RFC-4180-style CSV codec used by the Lean reference spec to load the PineScript
export fixtures under `tests/parity/baselines/` and to emit byte-identical
output for differential testing.

## Parity contract

`parseCsv` matches CPython's `csv.reader` (default dialect: `delimiter=','`,
`quotechar='"'`, `doublequote=True`, `strict=False`) and the Rust `csv` crate
closely enough for TradingView exports:

* A UTF-8 byte-order mark (`\xEF\xBB\xBF`, i.e. the char `U+FEFF`) at the very
  start of the input is stripped.
* Records are separated by `"\r\n"` or `"\n"` (both accepted, freely mixed).
  A final record without a trailing newline is kept; an empty trailing segment
  after the final newline is *not* a record.
* Fields are separated by `','`. A field whose *first* character is `'"'` is
  quoted: its content runs to the closing `'"'`, with `""` decoding to one
  literal quote; separators and newlines inside quotes are literal. Characters
  after the closing quote up to the next comma or record end are appended
  verbatim (lenient, like CPython with `strict=False`).
* No whitespace trimming, no type conversion.

`renderCsv` matches CPython's `csv.writer` with `QUOTE_MINIMAL` and
`lineterminator='\r\n'`: fields are joined by `','`, a field is quoted iff it
contains `','`, `'"'`, `'\r'` or `'\n'` (quotes doubled inside), and *every*
row — including the last — is terminated by `"\r\n"`.

## Documented deviations from CPython

* An empty line in the middle of the input parses to a record with one empty
  field `#[""]` (CPython yields a zero-field record `[]`); callers filter.
* `renderCsv` does not special-case a row whose only field is the empty
  string: it renders as a bare `"\r\n"` where CPython writes `'""\r\n'`, so
  byte-parity with CPython's writer holds for rows that are not exactly
  `#[""]`. Zero-field rows and `#[""]` rows render identically; the
  round-trip law `parseCsv (renderCsv rows) = rows` holds for matrices whose
  rows all have at least one field (`#[""]` included).
* A lone `'\r'` not followed by `'\n'` is an ordinary field character, not a
  record separator (CPython ends the record). Irrelevant for differential
  testing: both writers quote any field containing `'\r'`.

Golden-case theorems below pin the contract; they are mirrored as
identically-named tests in the target-language ports.
-/

set_option autoImplicit false

namespace LorentzianClassification

/-- Internal scanner state for `parseCsv`, mirroring CPython's `_csv.c`
field states (`START_FIELD` / `IN_FIELD` / `IN_QUOTED_FIELD` /
`QUOTE_IN_QUOTED_FIELD`). -/
private inductive FieldState where
  /-- At the start of a (possibly empty) field; a `'"'` here opens quoting. -/
  | fieldStart
  /-- Inside an unquoted field; `'"'` here is a literal character. -/
  | unquoted
  /-- Inside a quoted field; separators and newlines are literal. -/
  | quoted
  /-- Just saw a `'"'` inside a quoted field: either half of a doubled quote
  or the closing quote. -/
  | quoteInQuoted

/-- Tail-recursive scanner over the remaining characters. `field` is the field
being accumulated, `fields` the fields of the current record, `rows` the
completed records. Structurally recursive on the character list, so the
definition is total and available to future proofs. -/
private def parseLoop (st : FieldState) (field : String) (fields : Array String)
    (rows : Array (Array String)) : List Char → Array (Array String)
  | [] =>
    -- End of input: emit the dangling record unless we are exactly at the
    -- start of a record (empty input, or input ending in a newline).
    match st, fields.isEmpty with
    | .fieldStart, true => rows
    | _, _ => rows.push (fields.push field)
  | '"' :: cs =>
    match st with
    | .fieldStart => parseLoop .quoted field fields rows cs
    | .quoted => parseLoop .quoteInQuoted field fields rows cs
    | .quoteInQuoted => parseLoop .quoted (field.push '"') fields rows cs
    | .unquoted => parseLoop .unquoted (field.push '"') fields rows cs
  | ',' :: cs =>
    match st with
    | .quoted => parseLoop .quoted (field.push ',') fields rows cs
    | _ => parseLoop .fieldStart "" (fields.push field) rows cs
  | '\r' :: '\n' :: cs =>
    match st with
    | .quoted => parseLoop .quoted ((field.push '\r').push '\n') fields rows cs
    | _ => parseLoop .fieldStart "" #[] (rows.push (fields.push field)) cs
  | '\n' :: cs =>
    match st with
    | .quoted => parseLoop .quoted (field.push '\n') fields rows cs
    | _ => parseLoop .fieldStart "" #[] (rows.push (fields.push field)) cs
  | c :: cs =>
    match st with
    | .fieldStart | .quoteInQuoted => parseLoop .unquoted (field.push c) fields rows cs
    | _ => parseLoop st (field.push c) fields rows cs

/-- Parse CSV text into records of fields, RFC-4180-style, matching CPython's
`csv.reader` on TradingView exports (see the module docstring for the exact
parity contract and documented deviations).

* Strips a UTF-8 BOM (`U+FEFF`) at the very start if present.
* Accepts `"\r\n"` and `"\n"` record separators, freely mixed; keeps a final
  record that lacks a trailing newline; the empty segment after a final
  newline is not a record.
* `','` separates fields; a field starting with `'"'` is quoted (`""` decodes
  to a literal quote, separators/newlines inside quotes are literal, and text
  after the closing quote up to the next comma is appended verbatim).
* No trimming, no type conversion. An empty line in the middle parses to
  `#[""]` (one empty field); callers filter. -/
def parseCsv (content : String) : Array (Array String) :=
  match content.toList with
  | '\uFEFF' :: cs => parseLoop .fieldStart "" #[] #[] cs
  | cs => parseLoop .fieldStart "" #[] #[] cs

/-- `true` iff a field must be quoted under `QUOTE_MINIMAL`: it contains a
separator, a quote, or a newline character. -/
private def needsQuoting (f : String) : Bool :=
  f.foldl (init := false) fun b c =>
    b || c == ',' || c == '"' || c == '\r' || c == '\n'

/-- Encode one field: verbatim when no quoting is needed, otherwise wrapped in
quotes with every `'"'` doubled. -/
private def renderField (f : String) : String :=
  if needsQuoting f then
    (f.foldl (fun acc c => if c == '"' then acc ++ "\"\"" else acc.push c) "\"").push '"'
  else
    f

/-- Render records as CSV text, matching CPython's `csv.writer` with
`QUOTE_MINIMAL` and `lineterminator='\r\n'`: fields joined by `','`, a field
quoted iff it contains `','`, `'"'`, `'\r'` or `'\n'` (with `'"'` doubled
inside quotes), and every row — including the last — terminated by `"\r\n"`.

Note: unlike CPython, a row whose only field is `""` renders as a bare
`"\r\n"` rather than `'""\r\n'` (see the module docstring). -/
def renderCsv (rows : Array (Array String)) : String :=
  rows.foldl (init := "") fun acc row =>
    acc ++ String.intercalate "," (row.toList.map renderField) ++ "\r\n"

/-! ## Golden-case theorems

Concrete instances of the parity contract, machine-checked here and mirrored
as identically-named tests in every target-language port. Universally
quantified invariants (e.g. the round-trip law over all well-formed matrices)
are tracked as formalization debt and are exercised by randomized differential
testing in the meantime. -/

/-- A quoted field may contain the separator; empty fields are preserved. -/
theorem parseCsv_golden_quoted_comma :
    parseCsv "\"2026-01-01, 00:00\",1.2345,,x"
      = #[#["2026-01-01, 00:00", "1.2345", "", "x"]] := by
  native_decide

/-- A doubled quote inside a quoted field decodes to one literal quote. -/
theorem parseCsv_golden_escaped_quote :
    parseCsv "\"a\"\"b\"" = #[#["a\"b"]] := by
  native_decide

/-- `"\r\n"` and `"\n"` separators may be mixed, and a final record without a
trailing newline is kept (three of the four repo baselines end this way). -/
theorem parseCsv_golden_mixed_newlines_no_trailing :
    parseCsv "a,b\r\nc,d\ne,f" = #[#["a", "b"], #["c", "d"], #["e", "f"]] := by
  native_decide

/-- A UTF-8 BOM before the header is stripped; it is not part of the first
field. -/
theorem parseCsv_golden_bom_header :
    parseCsv "\uFEFFtime,open\r\n1771905600,63604.43\r\n"
      = #[#["time", "open"], #["1771905600", "63604.43"]] := by
  native_decide

/-- An empty line in the middle parses to a single empty field. -/
theorem parseCsv_golden_empty_line :
    parseCsv "a\n\nb\n" = #[#["a"], #[""], #["b"]] := by
  native_decide

/-- `QUOTE_MINIMAL`: only fields containing `','`, `'"'`, `'\r'` or `'\n'`
are quoted; quotes are doubled; every row is `"\r\n"`-terminated. -/
theorem renderCsv_golden_quote_minimal :
    renderCsv #[#["a,b", "c\"d", "e\nf", "plain", ""]]
      = "\"a,b\",\"c\"\"d\",\"e\nf\",plain,\r\n" := by
  native_decide

/-- Round trip on a representative matrix containing separators, quotes,
newlines and non-ASCII text. -/
theorem parseCsv_golden_roundtrip :
    parseCsv (renderCsv #[#["📈 up", "a,b", "c\"d"], #["\r\n", "", "x"]])
      = #[#["📈 up", "a,b", "c\"d"], #["\r\n", "", "x"]] := by
  native_decide

end LorentzianClassification
