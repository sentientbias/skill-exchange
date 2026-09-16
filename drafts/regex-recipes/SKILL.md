# Regex Recipes

Copy-paste regex patterns that actually work, with test strings and the
gotchas that bite you at 2am. Flavors covered: Python `re`, JavaScript,
and PCRE (grep -P, PHP). Differences are flagged where they matter.

Golden rules before the recipes:
1. **Test with both matches AND non-matches.** A pattern that matches
   everything is not a validator.
2. **Anchor when validating, don't anchor when searching.** `^...$` for
   "is this an email", bare pattern for "find emails in this text".
3. **Prefer simple over clever.** If the pattern needs a paragraph to
   explain, split it into two steps in code instead.

---

## Email address (practical, not RFC-perfect)

```
^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$
```

- Matches: `user.name+tag@example.com`
- Rejects: `user@`, `@example.com`, `user@example` (no TLD)
- Gotcha: this rejects some technically-valid RFC addresses (quoted
  strings, IP literals). That's fine — you want the 99.9% case, and the
  real validation is sending a confirmation email anyway.
- Never use regex alone to "verify" an email. Pattern-check, then send
  the confirmation link. That's the validation.

## URL (http/https)

```
https?://[^\s/$.?#].[^\s]*$
```

- Matches: `https://example.com/path?q=1#frag`
- For extracting URLs from text, drop the `$` anchor and use the
  global flag.
- Gotcha: trailing punctuation. In prose, `https://example.com.` often
  captures the period. Strip `[.,;:!?)]` from the end after matching:
  in Python, `url.rstrip('.,;:!?)')`.

## Phone numbers (international-ish)

```
^\+?[0-9][0-9\s().-]{6,18}[0-9]$
```

- Matches: `+1 (205) 555-0142`, `2055550142`, `+44 20 7946 0958`
- Rejects: `abc`, `123` (too short)
- This is intentionally loose. Phone formats vary too much for strict
  regex; pair it with a digit-count check:
  `len(re.sub(r'\D', '', s))` between 7 and 15.
- For US-only strictness: `^\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}$`

## Dates

ISO 8601 (strict-ish):
```
^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$
```

US MM/DD/YYYY:
```
^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/\d{4}$
```

- Gotcha: `2026-02-30` passes the pattern but isn't a real date. Regex
  checks *shape*, not *validity* — parse with a real date library after.
- Rule of thumb: regex for shape, `datetime.strptime` / `Date` for truth.

## IPv4

```
^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$
```

- Matches: `192.168.1.1`, rejects `999.1.1.1`
- The octet pattern `(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)` is the standard
  0-255 matcher — memorize it, it recurs everywhere.

## Semantic version

```
^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$
```

- Groups: 1=major, 2=minor, 3=patch, 4=prerelease, 5=build
- Matches: `1.2.3`, `2.0.0-beta.1+build.42`

## Hex color

```
^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$
```

- Matches `#fff`, `#ff8800`, `#ff8800cc` (with alpha). Rejects `#ffff`.

## Slug (URL-safe identifier)

```
^[a-z0-9]+(?:-[a-z0-9]+)*$
```

- Matches `my-cool-post-2`, rejects `-leading`, `trailing-`, `double--dash`,
  `UPPERCASE`, `under_scores`.

## Whitespace cleanup

Collapse runs of whitespace to single spaces:
```
# Python
re.sub(r'\s+', ' ', text).strip()
# JavaScript
text.replace(/\s+/g, ' ').trim()
```

Split on blank lines (paragraphs):
```
re.split(r'\n\s*\n', text)
```

## Numbers in text

Integer or decimal, optional sign:
```
^[+-]?(?:\d+\.?\d*|\.\d+)$
```

Thousands-separated (US):
```
^\d{1,3}(?:,\d{3})*(?:\.\d+)?$
```

- Gotcha: the second pattern rejects `1234` (no comma). If both plain
  and grouped numbers are valid input, strip commas first, then use the
  first pattern.

## Quoted strings (with escapes)

```
"([^"\\]|\\.)*"
```

- Matches `"say \"hi\""` correctly, stopping at the unescaped quote.
- The `([^"\\]|\\.)*` idiom — "not a quote or backslash, OR an escape
  sequence" — is the standard safe way to match quoted content.

## HTML tag stripper (quick and dirty)

```
<[^>]+>
```

- Replace with `''` to strip tags. Works for well-formed markup.
- Warning: this is a *stripper*, not a *parser*. For anything beyond
  quick cleanup, use a real HTML parser. Regex cannot parse HTML in
  the general case — nested tags, comments, and CDATA will fool it.

---

## Flavor differences that matter

| Feature | Python `re` | JavaScript | PCRE |
|---|---|---|---|
| Named groups | `(?P<name>...)` | `(?<name>...)` | `(?<name>...)` |
| Lookbehind | fixed-width only | fixed-width only (modern) | variable-width ok |
| `\d` | Unicode digits by default | ASCII unless `u` flag | ASCII unless Unicode mode |
| Dot-all | `re.DOTALL` | `s` flag | `s` modifier |

- Python gotcha: `\d` matches `٣` (Arabic-Indic digit) etc. Use
  `[0-9]` or `re.ASCII` when you mean ASCII digits.
- JavaScript gotcha: no `(?P<n>)` syntax; backreference to a named
  group is `\k<name>`.

## Readability: write regexes humans can maintain

Python verbose mode — comments and whitespace inside the pattern:
```python
phone_re = re.compile(r"""
    ^\+?              # optional country-code plus
    [0-9]             # first digit
    [0-9\s().-]{6,18} # middle chunk
    [0-9]$            # last digit
""", re.VERBOSE)
```

Named groups instead of numbered:
```python
m = re.match(r'(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})', s)
m.group('year')  # clearer than m.group(1)
```

## Catastrophic backtracking (ReDoS)

Nested quantifiers like `(a+)+$`, `(x+x+)+y` can hang forever on
non-matching input. Rules:

1. Never nest a quantifier inside a repeated group: `(a+)+` is the
   classic killer. Rewrite as `(a+)` or use atomic groups where
   supported: `(?>a+)+`.
2. Make alternatives mutually exclusive so the engine can't re-split
   the input many ways: `(?:ab|a)` is safer ordered as `(?:ab|a)`
   with the longer alternative first.
3. If a pattern comes from user input, set a timeout: Python 3.11+
   has no built-in regex timeout — run it in a thread with a deadline,
   or use the third-party `regex` module which supports `timeout=`.
4. Test every new pattern against a long non-matching string
   (`'a' * 50 + '!'`) and time it. If it takes >10ms, rewrite it.

## Quick reference

| Token | Meaning |
|---|---|
| `.` | any char except newline |
| `\d \w \s` | digit / word char / whitespace |
| `\D \W \S` | negations |
| `^ $` | start / end of string (or line with `m`) |
| `\b` | word boundary |
| `* + ?` | 0+, 1+, 0-or-1 (greedy) |
| `*? +? ??` | lazy versions |
| `{n} {n,} {n,m}` | exact / at-least / range |
| `(...)` | group; `(?:...)` non-capturing |
| `(?=...)` / `(?<=...)` | lookahead / lookbehind |
| `a\|b` | alternation |
| `[abc] [^abc] [a-z]` | char class / negated / range |
| `\1` or `\k<name>` | backreference |

## Debugging checklist

- [ ] Anchored (`^$`) if validating, unanchored if searching?
- [ ] Tested with inputs that should FAIL, not just pass?
- [ ] Escaped literal dots, parens, plus signs in the pattern?
- [ ] Character classes: is `-` at the start/end or escaped?
- [ ] Quantifier applied to what you think (group vs single char)?
- [ ] Timed against a long non-matching string (backtracking check)?
- [ ] Unicode behavior of `\d`, `\w` checked for this flavor?
