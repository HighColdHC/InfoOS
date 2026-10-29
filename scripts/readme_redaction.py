"""Screenshot-only redaction; never publish source values or derived dictionaries."""
from __future__ import annotations

import json
import ast
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).with_name("readme-redaction.json")


def policy() -> dict:
    rules = json.loads(POLICY_PATH.read_text())
    rules["interface_literals"] = interface_literals()
    return rules


@lru_cache(maxsize=1)
def interface_literals() -> list[str]:
    """Recognize product copy from source, without whitelisting private DB values."""
    values = set()
    root = POLICY_PATH.parent.parent / "frontend/src"
    for path in root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or any(
            part in {"__tests__", "fixtures"} or ".test." in part or ".spec." in part for part in path.parts
        ):
            continue
        source = path.read_text()
        texts = re.findall(r"(?:>|})([^<>{}]+)(?=<|{)", source)
        texts += [match[1] for match in re.findall(r"(['\"`])((?:\\.|(?!\1).)*?)\1", source)]
        for text in texts:
            normalized = " ".join(text.split())
            if normalized and "${" not in normalized:
                values.add(normalized)
    # Knowledge subtype and field labels are rendered from the public domain
    # schema, rather than TSX literals. Never derive this allowlist from DB text.
    schema = root.parents[1] / "backend/domain/intelligence/knowledge_kinds.py"
    for node in ast.walk(ast.parse(schema.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(" ".join(node.value.split()))
    return sorted(values)


def scrub_copy(path: Path) -> list[str]:
    """Remove credential records from the local copy without reading their values."""
    rules = policy()
    removed = []
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA secure_delete=ON")
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if any(re.search(pattern, table, re.I) for pattern in rules["private_table_patterns"]):
                db.execute(f"DELETE FROM {quoted}")
                removed.append(table)
                continue
            for column in db.execute(f"PRAGMA table_info({quoted})").fetchall():
                if re.search(rules["private_column_pattern"], column[1], re.I):
                    name = '"' + column[1].replace('"', '""') + '"'
                    value = "''" if column[3] else "NULL"
                    db.execute(f"UPDATE {quoted} SET {name}={value} WHERE {name} IS NOT NULL AND {name} != ''")
        if "settings" in tables:
            for (key,) in db.execute("SELECT key FROM settings").fetchall():
                if re.search(rules["private_column_pattern"], key, re.I):
                    db.execute("DELETE FROM settings WHERE key=?", (key,))
        db.commit()
        db.execute("VACUUM")
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    return removed


def corpus(path: Path) -> dict[str, set[str]]:
    """Read noncredential text only; keep the private comparison corpus in memory."""
    rules = policy()
    result = {kind: set() for kind in ("identity", "heading", "body")}
    safe = set(rules["preserve_literals"])

    def add(value, kind):
        if not isinstance(value, str):
            return
        value = value.strip()
        if len(value) >= (2 if kind == "identity" else 4) and value not in safe and not re.fullmatch(r"[\d\W_]+", value):
            result[kind].add(value)

    def unpack(value, key="content"):
        if isinstance(value, dict):
            for k, v in value.items():
                if not re.search(rules["private_column_pattern"], k, re.I):
                    unpack(v, k if re.search(r"title|name|author|attribution_ref", k) else "content")
        elif isinstance(value, list):
            for part in value:
                unpack(part, key)
        elif re.search(r"title|name|author|attribution_ref|quote|summary|note|description|content|body|text|reason|message|problem|solution|audience|evidence|value", key):
            add(value, "heading" if "title" in key else "body")

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if any(re.search(pattern, table, re.I) for pattern in rules["private_table_patterns"]):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({quoted})")}
            for kind, candidates in rules["audit_columns"].items():
                for column in columns.intersection(candidates):
                    for (value,) in db.execute(f'SELECT DISTINCT "{column}" FROM {quoted} WHERE "{column}" IS NOT NULL'):
                        if kind == "json":
                            if value:
                                try:
                                    unpack(json.loads(value))
                                except (ValueError, TypeError):
                                    pass
                        else:
                            add(value, kind)
    result["identity"].add("demo-admin")
    return result


class LeakAudit:
    """Substring comparison, including prose fragments, with no raw report."""

    def __init__(self, values):
        self.safe = set(policy()["preserve_literals"])
        self.interface = set(interface_literals())
        self.short = {}
        self.fragments = {}
        for kind, strings in values.items():
            for value in strings:
                if len(value) < 12:
                    self.short[value] = kind
                else:
                    # Overlapping windows catch excerpts whose start differs from the DB value.
                    for index in range(0, len(value) - 11, 6):
                        part = value[index:index + 12]
                        if sum(char.isalpha() for char in part) >= 6:
                            self.fragments[part] = kind
        self.pattern = re.compile("|".join(re.escape(s) for s in sorted(self.short, key=len, reverse=True))) if self.short else None

    def inspect(self, entries):
        hits = {}
        indices = []
        for index, value in enumerate(entries):
            parts = re.split(r"[、·,\s]+", value.strip())
            composed = bool(parts) and all(p in self.interface or p in self.safe or re.fullmatch(r"v?\d+", p) for p in parts)
            if value.strip() in self.safe or " ".join(value.split()) in self.interface or composed:
                continue
            categories = set()
            if self.pattern:
                categories.update(self.short[m.group()] for m in self.pattern.finditer(value))
            categories.update(self.fragments[value[i:i + 12]] for i in range(len(value) - 11)
                              if value[i:i + 12] in self.fragments)
            if categories:
                indices.append(index)
                for kind in categories:
                    hits[kind] = hits.get(kind, 0) + 1
        return {"count": len(indices), "categories": hits}, indices


class IdentifierAudit:
    """OCR/source scan: whole identifiers, not substrings of ordinary words."""

    def __init__(self, identifiers):
        rules = policy()
        self.safe = set(rules["preserve_literals"]) | set(rules["interface_literals"])
        parts = []
        for value in sorted(identifiers - self.safe - {"demo-admin"}, key=len, reverse=True):
            escaped = re.escape(value)
            parts.append(r"(?<!\w)" + escaped + r"(?!\w)")
        self.pattern = re.compile("|".join(parts)) if parts else None

    def inspect(self, entries):
        return sum(1 for value in entries if " ".join(value.split()) not in self.safe
                   and self.pattern is not None and self.pattern.search(value))


DOM_REDACTION = r"""({rules, identities, headings}) => {
  const safe = new Set(rules.preserve_literals);
  const ui = new Set(rules.interface_literals);
  const isUi = value => ui.has(value.trim().replace(/\s+/g, ' '));
  const identitySet = new Set(identities), headingSet = new Set(headings);
  const hash = value => { let h = 2166136261; for (const c of value) h = Math.imul(h ^ c.codePointAt(0), 16777619); return h >>> 0; };
  const cache = new Map();
  const identifier = original => {
    const trimmed = original.trim(), match = trimmed.match(/^([^·]+) · /);
    const lead = match && safe.has(match[1].trim()) ? match[0] : '';
    const value = trimmed.slice(lead.length);
    let seed = hash(trimmed), out = '';
    for (let i = 0; i < value.length; i++) {
      seed = Math.imul(seed ^ (i + 1), 16777619) >>> 0;
      const c = value[i];
      out += /[\p{L}\p{N}]/u.test(c) ? '0123456789abcdef'[(seed >>> 16) % 16] : c;
    }
    return (original.match(/^\s*/)?.[0] || '') + lead + out + (original.match(/\s*$/)?.[0] || '');
  };
  const fictional = (original, kind = 'body', force = false) => {
    if (kind === 'identifier') return !force && safe.has(original.trim()) ? original : identifier(original);
    if (kind === 'body' && headingSet.has(original.trim())) kind = 'heading';
    else if (kind === 'body' && identitySet.has(original.trim())) kind = 'identity';
    if (!force && (!original.trim() || safe.has(original.trim()) || kind !== 'identity' && /^[\d\s\W_]+$/u.test(original) && !/\p{L}/u.test(original))) return original;
    const prefix = rules.label_prefixes.find(p => original.startsWith(p));
    if (prefix && !force) return prefix + fictional(original.slice(prefix.length), prefix === '与你的关系：' ? 'body' : 'identity');
    const cacheKey = kind + '\0' + original;
    if (!force && cache.has(cacheKey)) return cache.get(cacheKey);
    const seed = hash(original.trim());
    // Short account names also keep attribution chips within the product layout.
    const pool = kind === 'identity' ? rules.pools.identity.filter(s => s.length <= 2) : rules.pools[kind];
    const target = Math.min(original.trim().length, 800);
    const ranked = [...pool].sort((a,b) => Math.abs(a.length-target)-Math.abs(b.length-target));
    const close = ranked.filter(s => Math.abs(s.length-target) <= Math.abs(ranked[0].length-target) + 6);
    let text = close[seed % close.length];
    if (kind === 'body') {
      let i = seed;
      while (text.length < target - 20) text += pool[++i % pool.length];
    }
    // Prose is fictional; statistics and standalone dates/numbers are preserved separately.
    const result = (original.match(/^\s*/)?.[0] || '') + text + (original.match(/\s*$/)?.[0] || '');
    cache.set(cacheKey, result); return result;
  };
  const patterns = rules.text_patterns.map(value => new RegExp(value, 'giu'));
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const identityPattern = identities.length ? new RegExp(identities.map(escape).join('|'), 'gu') : null;
  const headingPattern = headings.length ? new RegExp(headings.map(escape).join('|'), 'gu') : null;
  const generic = value => {
    if (safe.has(value.trim())) return value;
    if (headingSet.has(value.trim())) return fictional(value, 'heading');
    const numbered = value.match(/^(\s*\d+[.\s]+)(.+)$/su);
    if (numbered && headingSet.has(numbered[2].trim())) return numbered[1] + fictional(numbered[2], 'heading');
    const replacements = [];
    let result = value;
    for (const pattern of patterns) result = result.replace(pattern, match => {
      let replacement;
      if (match.includes('@') && !match.startsWith('@')) replacement = 'reader@example.com';
      else if (/^https?:|^(?:[\w-]+\.)+[a-z]{2,}/i.test(match)) replacement = 'https://example.org/resource';
      else if (match.startsWith('@')) replacement = '@open_notes';
      else if (/^(?:\d{1,3}\.){3}/.test(match)) replacement = 'example.com';
      else if (/^(?:ios_|sk-|ou_|cli_|tok_|token_|card_|know_|src_|run_|task_|worker_|capture_|raw_|batch_|incident_)|^[a-f0-9]{24,}$/i.test(match)) replacement = fictional(match, 'identifier');
      else if (match.startsWith('/')) replacement = '/example/workspace';
      else replacement = fictional(match, 'identity');
      replacements.push(replacement);
      return '\uE000' + (replacements.length - 1) + '\uE001';
    });
    if (headingPattern) result = result.replace(headingPattern, text => fictional(text, 'heading'));
    if (identityPattern) result = result.replace(identityPattern, text => safe.has(text) ? text : fictional(text, 'identity'));
    return result.replace(/\uE000(\d+)\uE001/g, (_, i) => replacements[Number(i)]);
  };
  const nodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, element = node.parentElement;
    if (!element || element.closest('script,style')) continue;
    nodes.push(node);
    const original = node.nodeValue;
    if (element.closest(rules.preserve_selectors.join(','))) continue;
    if (element.closest(rules.identifier_selectors.join(','))) {
      if (original.trim()) node.nodeValue = fictional(original, 'identifier');
      continue;
    }
    const privateElement = element.closest(rules.selectors.filter(s => !['img','video','canvas'].includes(s)).join(','));
    if (!privateElement && isUi(original)) continue;
    if (privateElement) {
      const kind = element.closest('h1,h2,h3') ? 'heading' : element.closest('.collector-log-table') ? 'log' :
        element.closest(rules.identity_selectors.join(',')) ? 'identity' : 'body';
      const hasHeading = headingPattern?.test(original);
      if (headingPattern) headingPattern.lastIndex = 0;
      const clean = generic(original);
      node.nodeValue = kind === 'identity' ? fictional(original, 'identity') : hasHeading || identitySet.has(original.trim()) ? clean :
        fictional(kind === 'identity' ? original : clean, kind);
    } else node.nodeValue = generic(original);
  }
  const attributes = [];
  for (const element of document.querySelectorAll('*')) {
    if (element.matches('script,link,style')) continue;
    for (const attr of ['title','alt','data-tooltip','aria-label','placeholder','href','src']) {
      if (!element.hasAttribute(attr)) continue;
      const value = element.getAttribute(attr);
      const clean = ['href','src'].includes(attr) ? generic(value) :
        safe.has(value.trim()) || isUi(value) ? value : fictional(generic(value), attr === 'alt' ? 'identity' : 'body');
      element.setAttribute(attr, clean); attributes.push([element,attr]);
    }
    if (element.matches('img,video,canvas')) element.style.visibility = 'hidden';
    if (element.matches('input,textarea') && !element.matches('[type=number],[type=time],[type=datetime-local],[type=checkbox],[type=radio]')) {
      if (element.value) element.value = fictional(generic(element.value), 'identity');
    }
  }
  document.title = generic(document.title);
  window.__readmeRedaction = {nodes,attributes,fictional,generic,rules};
}"""

DOM_EXPORT = r"""() => {
  const s = window.__readmeRedaction;
  return [...s.nodes.map(n => n.nodeValue), ...s.attributes.map(([e,a])=>e.getAttribute(a)||''),
    ...[...document.querySelectorAll('input,textarea')].map(e=>e.value), document.title];
}"""


def redact(page, values: dict[str, set[str]], audit: LeakAudit):
    original_entries = page.evaluate("""() => {
      const entries = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (!walker.currentNode.parentElement?.closest('script,style')) entries.push(walker.currentNode.nodeValue);
      }
      for (const e of document.querySelectorAll('*:not(script):not(link):not(style)')) {
        for (const a of ['title','alt','data-tooltip','aria-label','placeholder','href','src']) {
          if (e.hasAttribute(a)) entries.push(e.getAttribute(a));
        }
        if (e.matches('input,textarea')) entries.push(e.value);
      }
      entries.push(document.title);
      return entries;
    }""")
    original, _ = audit.inspect(original_entries)
    page.evaluate(DOM_REDACTION, {"rules": policy(), "identities": sorted(values["identity"], key=len, reverse=True),
                                         "headings": sorted(values["heading"], key=len, reverse=True)})
    initial, indices = audit.inspect(page.evaluate(DOM_EXPORT))
    # A fictional value can itself overlap a different original value. Retry
    # only the flagged entries, keeping their field type on every pass.
    for _ in range(12):
        if not indices:
            break
        page.evaluate(r"""indices => {
          const s = window.__readmeRedaction, inputs = [...document.querySelectorAll('input,textarea')];
          for (let i of indices) {
            if (i < s.nodes.length) {
              const n=s.nodes[i], e=n.parentElement;
              const kind=e?.closest(s.rules.identifier_selectors.join(',')) ? 'identifier' :
                e?.closest(s.rules.identity_selectors.join(',')) ? 'identity' : 'body';
              n.nodeValue=s.fictional(n.nodeValue, kind, true); continue;
            }
            i -= s.nodes.length;
            if (i < s.attributes.length) { const [e,a]=s.attributes[i]; e.setAttribute(a,s.fictional(e.getAttribute(a), 'body', true)); continue; }
            i -= s.attributes.length;
            if (i < inputs.length) inputs[i].value=s.fictional(inputs[i].value, 'identity', true);
            else document.title=s.fictional(document.title, 'heading', true);
          }
        }""", indices)
        final, indices = audit.inspect(page.evaluate(DOM_EXPORT))
    final, indices = audit.inspect(page.evaluate(DOM_EXPORT))
    if final["count"]:
        shapes = page.evaluate("""indices => {
          const s=window.__readmeRedaction;
          return indices.map(i => i < s.nodes.length ? {
            tag:s.nodes[i].parentElement?.tagName, cls:s.nodes[i].parentElement?.className,
            identifier:!!s.nodes[i].parentElement?.closest(s.rules.identifier_selectors.join(','))
          } : {other:true});
        }""", indices)
        raise RuntimeError("Private text audit failed: " + json.dumps({**final, "shapes": shapes}))
    return {"before": original, "repaired": initial, "final": final}
