/* highlight.js — the semantic syntax register of the RXF web surface (docs/21).
 *
 * One small, dependency-free module: a pane declares its language (data-lang) and this module
 * colors what the tokens MEAN, not just what they look like. The registers are keyed by
 * semantic class: a handle (#123, an IR value) is an addressable cell, a RefKind value is an
 * edge kind, a kind word (NodeKind/Form/...) names the format, an opcode word is what runs, a
 * Doc word is recorded intent (docs/22), and an XML element name is a Type name of the delta
 * (docs/15). Everything a class looks like lives in ui.css under the m- prefix — this file
 * never paints, it only classifies and emits spans in ONE pass per pane.
 *
 * The vocabulary is data, not code paths: REGISTER.enums is the words of the format (the
 * ground enumerations of core/kinds and the starter kinds, transcribed from the one meaning
 * table tools/highlight/meaning.json and core/kinds.pyi). tests/test_server_views.py pins the
 * register back to those tables through node, so it cannot drift. Language word lists (C, LLVM
 * IR, Python) are the languages' own vocabularies, never the RXF one.
 *
 * Lexers: rxfd/xml (the delta documents), c, cpp (c + the C++ face), llvm (the import entrance
 * format, docs/22), python (future strands), text (diagnostics and prose: handles, paths,
 * enum words). The source mirror of the editor keeps the one meaning table's marks as its
 * green/red layer and takes its base colors from here (RXFHighlight.decorate).
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RXFHighlight = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // ---- the class register: what each semantic class MEANS (its look lives in ui.css) --------
  const CLASSES = {
    handle: "an addressable cell: a handle (#42), an IR value or global (%x, @g)",
    path: "an addressable name: an object path (pymergetic.rxf...), an alias path (@call.callee)",
    tag: "an XML element — the name of a Type of the image (docs/15)",
    ns: "the format's own namespace (rxf:) — the format speaking, never a field",
    attr: "an XML attribute — a field name of the type",
    refkind: "a RefKind word: what an edge is for (Call | Data | Entry | Type | ...)",
    effects: "an effects word: what a body does (Read | Write | Allocate | Host | Time)",
    kind: "a kind word: NodeKind / Form / TypeFlags / ... — a word of the format",
    opcode: "an opcode word: Opcode / NumericOperation / Intrinsic / Terminator",
    doc: "a Doc or provenance marker: rxf:doc, DocKind/DocRole/DocStatus/DocCodec, TODO, was-once-Python",
    string: "a string",
    number: "a number",
    comment: "a comment",
    keyword: "a keyword of the language",
    builtin: "a builtin of the language",
    type: "a type word",
    fn: "a function name",
    operator: "an operator",
    punct: "punctuation",
    pre: "a preprocessor line or directive",
    meta: "IR metadata that is not provenance (!123, !name)",
    escape: "an escape or entity",
    decor: "a Python decorator",
    identifier: "a name",
    plain: "the space and plain names around the meaning tokens — rendered bare",
  };

  // ---- the words of the format: type name -> semantic class and its enumerators --------------
  // The enumerators are pinned by tests/test_server_views.py to core/kinds (core/kinds.pyi and
  // the Doc slice of core/kinds/kinds.cpp) and to tools/highlight/meaning.json (which is itself
  // pinned to the starter image and native tables) — never edited by hand in two places.
  const ENUMS = {
    NodeKind: { c: "kind", words: ["Root", "Group", "Namespace", "Card", "Type", "Field", "Fn", "Code", "Data", "Section", "Limit", "Import", "Export", "View", "Tool", "Blob", "Channel", "Transaction", "Module", "Enumerator", "Alias"] },
    Form: { c: "kind", words: ["Meta", "Void", "Bool", "Uint", "Sint", "Float", "Bytes", "Struct", "Union", "Enum", "Array", "Ref", "Function", "Control", "Expression", "Execution", "Reserved16", "Opaque"] },
    TypeFlags: { c: "kind", words: ["Variable", "Signed", "SelfDescribing", "Bitset"] },
    FieldFlags: { c: "kind", words: ["Mutable", "Optional", "Repeated", "Ref", "Children", "Inverse"] },
    Binding: { c: "kind", words: ["Mandatory", "Optional"] },
    OwnerKind: { c: "kind", words: ["System", "User", "Application", "Build", "Device", "External", "Shared"] },
    Mobility: { c: "kind", words: ["Movable", "Pinned", "Relocatable"] },
    RefKind: { c: "refkind", words: ["Call", "Data", "Entry", "Type", "Import", "Implementation", "Owner", "Doc"] },
    Effects: { c: "effects", words: ["Read", "Write", "Allocate", "Host", "Time"] },
    DocKind: { c: "doc", words: ["Narrates", "Topic", "Todo"] },
    DocStatus: { c: "doc", words: ["Open", "Resolved"] },
    DocCodec: { c: "doc", words: ["Lz4", "Raw"] },
    DocRole: { c: "doc", words: ["Narrates", "Attaches", "References", "Parent", "Related"] },
    Opcode: { c: "opcode", words: ["BeginPrivate", "Allocate", "Copy", "Read", "Write", "Validate", "Publish", "Rollback", "Cleanup", "ReadTag", "ProjectPayload", "ConstructOption", "ConstructResult", "IterInit", "IterCondition", "IterProject", "IterAdvance", "CallCallback", "ReturnValue", "MapRefusal", "EnrichRefusal", "Compare", "Hash", "Utf8Validate", "Utf8Boundary", "FormatInteger", "AppendBytes", "Search", "Lookup", "CanonicalOrder", "MoveSlot", "Pin", "Unpin", "Borrow", "ReleaseBorrow", "StoreField"] },
    NumericOperation: { c: "opcode", words: ["Equal", "Less", "Minimum", "Maximum", "WrappingAdd", "CheckedAdd", "SaturatingAdd", "WrappingSubtract", "CheckedSubtract", "SaturatingSubtract", "WrappingMultiply", "CheckedMultiply", "SaturatingMultiply", "Divide", "Remainder", "BitNot", "BitAnd", "BitOr", "BitXor", "ShiftLeft", "ShiftRight", "CheckedNegate", "CheckedAbsolute", "Convert"] },
    Intrinsic: { c: "opcode", words: ["If", "While", "Switch", "Sequence", "Return", "Break", "Continue", "Refuse"] },
    Terminator: { c: "opcode", words: ["Goto", "Branch", "SwitchTag", "Return", "Refuse", "Loop"] },
    RefusalCode: { c: "kind", words: ["Overflow", "DivisionByZero", "ShiftCount", "NonFinite", "Inexact", "OutOfRange", "InvalidUtf8", "OutOfMemory", "BorrowConflict", "Pinned", "Alignment", "CapabilityRefused", "CapabilityUnavailable", "CapabilityInvalid"] },
    ModuleCategory: { c: "kind", words: ["Schema", "Library", "Application"] },
    ModuleFlags: { c: "kind", words: ["Namespace"] },
    AliasFlags: { c: "kind", words: ["Reexport"] },
  };

  // The bare-word index: first claimant wins, kind words before edge words (a bare "Data" is
  // most often NodeKind.Data; a pane that knows better (a reference's kind column) says so).
  const PRECEDENCE = ["NodeKind", "Form", "TypeFlags", "FieldFlags", "Binding", "OwnerKind", "Mobility", "RefKind", "Effects", "DocKind", "DocStatus", "DocCodec", "DocRole", "Opcode", "NumericOperation", "Intrinsic", "Terminator", "RefusalCode", "ModuleCategory", "ModuleFlags", "AliasFlags"];
  const WORDS = {};
  for (const type of PRECEDENCE) {
    const entry = ENUMS[type];
    for (const word of entry.words) {
      if (!(word in WORDS)) WORDS[word] = entry.c;
      // rxf:kind names the NodeKind of a cell in shouty words (MODULE, FN, CODE, ...).
      if (type === "NodeKind") {
        const shouty = word.toUpperCase();
        if (!(shouty in WORDS)) WORDS[shouty] = entry.c;
      }
    }
  }

  function classOfType(type) {
    return type in ENUMS ? ENUMS[type].c : "kind";
  }

  function classifyWord(word) {
    return word in WORDS ? WORDS[word] : null;
  }

  // An enum value of a delta: `RefKind.Call`, `@kinds.Effects.Write|Allocate`, `Form.Struct`.
  // The final segment is capitalized and the tail may be a bitset; a known type name says the
  // class, a capitalized type name of the data is a kind word, a lowercase one is a path.
  const ENUM_VALUE = /^(?:@?[A-Za-z_][\w-]*\.)*([A-Za-z_][\w-]*)\.([A-Z][\w-]*(?:\|[A-Z][\w-]*)*)$/;
  const WORD_ONLY = /^[A-Za-z_][\w-]*$/;

  function classifyValue(value) {
    const dotted = ENUM_VALUE.exec(value);
    if (dotted) {
      const type = dotted[1];
      if (type in ENUMS) return ENUMS[type].c;
      return /^[A-Z]/.test(type) ? "kind" : "path";
    }
    if (value === "true" || value === "false") return "builtin";
    if (/^(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)$/.test(value)) return "number";
    if (/^(?:[0-9a-fA-F]{2}){2,}$/.test(value)) return "number"; // a hex byte string (rxf:data)
    if (WORD_ONLY.test(value)) {
      const known = classifyWord(value);
      if (known) return known;
      return "path"; // a word is the path of an object (docs/15)
    }
    if (/^@?[A-Za-z_][\w-]*(?:\.[\w-]+)+$/.test(value)) return "path"; // an object path of the selfedit graph
    return null;
  }

  // ---- the scanners: one pass per pane, tokens are [start, end, class) ------------------------
  // The stream is an exact partition of the pane: every character lands in exactly one token,
  // and the runs between the meaningful ones are "plain" (rendered bare, never a DOM node).

  function push(tokens, s, e, c) {
    if (e > s && c) tokens.push({ s: s, e: e, c: c });
  }

  function cover(text, raw) {
    const tokens = [];
    let at = 0;
    for (const t of raw) {
      if (t.e <= at) continue; // a boundary slip must not garble or double-cover the text
      const s = Math.max(t.s, at);
      if (s > at) tokens.push({ s: at, e: s, c: "plain" });
      tokens.push({ s: s, e: t.e, c: t.c });
      at = t.e;
    }
    if (at < text.length) tokens.push({ s: at, e: text.length, c: "plain" });
    return tokens;
  }

  // text: diagnostics and prose. Handles, paths, enum words, numbers, strings, markers.
  const TEXT_RE = new RegExp(
    [
      "\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*'",           // string
      "#[0-9]+",                                                  // a cell handle
      "\\b(?:TODO|FIXME|XXX)\\b",                                 // a doc marker
      "was once Python",                                          // the provenance marker (docs/22)
      "(?:@?[A-Za-z_][\\w-]*\\.)*([A-Za-z_][\\w-]*)\\.([A-Z][\\w-]*(?:\\|[A-Z][\\w-]*)*)", // enum value
      "@[A-Za-z_][\\w-]*(?:\\.[\\w-]+)*",                         // alias path
      "[A-Za-z_][\\w-]*(?:\\.[\\w-]+)+(?:\\[\\d+\\])?",           // object path
      "0[xX][0-9a-fA-F]+|\\d+(?:\\.\\d+)?",                       // number
      "[A-Za-z_][\\w-]*(?:\\[\\d+\\])?",                          // word
    ].join("|"),
    "g"
  );

  function lexText(text) {
    const tokens = [];
    let m;
    TEXT_RE.lastIndex = 0;
    while ((m = TEXT_RE.exec(text)) !== null) {
      const face = m[0];
      let c;
      if (face[0] === '"' || face[0] === "'") {
        // A quoted string that is one semantic value shows its meaning (the diagnostics quote
        // enum words and paths in refusals); anything else stays a string. The regex only
        // matches closed strings, so face starts and ends with its quote.
        const inner = face.slice(1, -1);
        const innerCls = classifyValue(inner);
        if (innerCls) {
          push(tokens, m.index, m.index + 1, "string");
          push(tokens, m.index + 1, m.index + face.length - 1, innerCls);
          push(tokens, m.index + face.length - 1, m.index + face.length, "string");
          continue;
        }
        c = "string";
      } else if (face[0] === "#") c = "handle";
      else if (face === "was once Python" || face === "TODO" || face === "FIXME" || face === "XXX") c = "doc";
      else if (m[2] !== undefined) {
        const type = m[1];
        c = type in ENUMS ? ENUMS[type].c : /^[A-Z]/.test(type) ? "kind" : "path";
      } else if (face[0] === "@") c = "path";
      else if (face.includes(".")) c = "path";
      else if (/^[0-9]/.test(face)) c = "number";
      else c = classifyWord(face);
      push(tokens, m.index, m.index + face.length, c);
    }
    return tokens;
  }

  // rxfd / xml: the delta documents (docs/15). Tag names are Type names, attributes are fields,
  // and the VALUE says what it is: an enum word, an alias path, an object path, or a number.
  function lexXml(text) {
    const tokens = [];
    const stack = [];
    let pos = 0;
    const len = text.length;
    while (pos < len) {
      const at = text.indexOf("<", pos);
      const textEnd = at === -1 ? len : at;
      if (textEnd > pos) {
        const top = stack[stack.length - 1];
        emitText(text, pos, textEnd, tokens, top === "rxf:doc" ? "doc" : null);
      }
      if (at === -1) break;
      if (text.startsWith("<!--", at)) {
        const end = text.indexOf("-->", at + 4);
        const stop = end === -1 ? len : end + 3;
        push(tokens, at, stop, "comment");
        pos = stop;
        continue;
      }
      if (text.startsWith("<?", at)) {
        const end = text.indexOf("?>", at + 2);
        const stop = end === -1 ? len : end + 2;
        push(tokens, at, stop, "pre");
        pos = stop;
        continue;
      }
      pos = lexTag(text, at, tokens, stack);
    }
    return tokens;
  }

  function emitText(text, from, to, tokens, whole) {
    let at = from;
    const entity = /&[a-zA-Z#][a-zA-Z0-9]*;/g;
    entity.lastIndex = from;
    let m;
    while ((m = entity.exec(text)) !== null && m.index < to) {
      if (m.index > at) push(tokens, at, m.index, whole);
      push(tokens, m.index, m.index + m[0].length, "escape");
      at = m.index + m[0].length;
    }
    if (to > at) push(tokens, at, to, whole);
  }

  function lexTag(text, pos, tokens, stack) {
    const len = text.length;
    let at = pos;
    if (text.startsWith("</", at)) {
      push(tokens, at, at + 2, "punct");
      at += 2;
    } else {
      push(tokens, at, at + 1, "punct");
      at += 1;
    }
    const closing = text[pos + 1] === "/";
    const name = /^[A-Za-z_][\w.:-]*/.exec(text.slice(at, at + 256));
    let full = "";
    if (name) {
      full = name[0];
      const colon = full.indexOf(":");
      if (colon >= 0) {
        push(tokens, at, at + colon + 1, "ns");
        push(tokens, at + colon + 1, at + full.length, full === "rxf:doc" ? "doc" : "tag");
      } else {
        push(tokens, at, at + full.length, "tag");
      }
      at += full.length;
    }
    if (closing) stack.pop();
    else stack.push(full);
    // attributes
    while (at < len) {
      while (at < len && /\s/.test(text[at])) at += 1;
      if (at >= len) break;
      if (text.startsWith("/>", at)) {
        push(tokens, at, at + 2, "punct");
        if (!closing) stack.pop();
        return at + 2;
      }
      if (text[at] === ">") {
        push(tokens, at, at + 1, "punct");
        return at + 1;
      }
      const attr = /^[A-Za-z_][\w.:-]*/.exec(text.slice(at, at + 256));
      if (!attr) {
        at += 1;
        continue;
      }
      const aname = attr[0];
      const acolon = aname.indexOf(":");
      if (acolon >= 0) {
        push(tokens, at, at + acolon + 1, "ns");
        push(tokens, at + acolon + 1, at + aname.length, "attr");
      } else {
        push(tokens, at, at + aname.length, "attr");
      }
      at += aname.length;
      while (at < len && /\s/.test(text[at])) at += 1;
      if (text[at] === "=") {
        push(tokens, at, at + 1, "punct");
        at += 1;
        while (at < len && /\s/.test(text[at])) at += 1;
        const quote = text[at];
        if (quote === '"' || quote === "'") {
          const end = text.indexOf(quote, at + 1);
          const stop = end === -1 ? len : end + 1;
          lexValue(text, at, stop, tokens);
          at = stop;
        }
      }
    }
    return at;
  }

  function lexValue(text, from, to, tokens) {
    const inner = text.slice(from + 1, Math.max(from + 1, to - 1));
    const cls = classifyValue(inner) || "string";
    push(tokens, from, from + 1, "punct");
    emitText(text, from + 1, Math.max(from + 1, to - 1), tokens, cls);
    if (to - 1 > from) push(tokens, to - 1, to, "punct");
  }

  // c / cpp: the faces of the source panes. stdint types and words like return are the
  // language's own vocabulary; includes land in the path register (they are addresses too).
  const C_KEYWORDS = new Set(("auto break case char const continue default do double else enum extern for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert _Thread_local " +
    "alignas alignof and and_eq asm bitand bitor bool catch class compl concept consteval constexpr constinit const_cast decltype delete dynamic_cast explicit export false friend mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public reinterpret_cast requires static_assert template this thread_local throw true try typeid typename using virtual wchar_t char8_t char16_t char32_t xor xor_eq").split(" "));
  const C_TYPES = new Set(("void char short int long float double signed unsigned size_t ptrdiff_t intptr_t uintptr_t uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t FILE va_list jmp_buf").split(" "));

  function lexCLike(text, cpp) {
    const tokens = [];
    const re = new RegExp(
      [
        "//[^\\n]*",                                                    // line comment
        "/\\*[\\s\\S]*?(?:\\*/|$)",                                     // block comment
        "\"(?:\\\\.|[^\"\\\\\\n])*\"?|'(?:\\\\.|[^'\\\\\\n])'?",        // string or char
        "#[ \\t]*[A-Za-z_]\\w*",                                        // preprocessor directive
        "0[xX][0-9a-fA-F]+[uUlL]*|\\d+(?:\\.\\d*)?(?:[eE][+-]?\\d+)?[fFuUlL]*", // number
        "[A-Za-z_]\\w*",                                                // word
        "<<|>>|->|==|!=|<=|>=|&&|\\|\\||\\+\\+|--|[+\\-*/%&|^~!<>=?:.,;(){}\\[\\]]", // operator or punct
      ].join("|"),
      "g"
    );
    let m;
    let expectHeader = false;
    while ((m = re.exec(text)) !== null) {
      const face = m[0];
      const first = face[0];
      if (first === "/" && (face[1] === "/" || face[1] === "*")) push(tokens, m.index, m.index + face.length, "comment");
      else if (first === '"' || first === "'") push(tokens, m.index, m.index + face.length, "string");
      else if (first === "#") {
        push(tokens, m.index, m.index + face.length, "pre");
        expectHeader = /include|import/.test(face);
      } else if (expectHeader && (first === "<" || first === '"')) {
        push(tokens, m.index, m.index + face.length, "path");
        expectHeader = false;
      } else if (/[0-9]/.test(first)) push(tokens, m.index, m.index + face.length, "number");
      else if (/[A-Za-z_]/.test(first)) {
        let cls = null;
        if (C_TYPES.has(face)) cls = "type";
        else if (C_KEYWORDS.has(face)) cls = "keyword";
        else {
          const after = text.slice(m.index + face.length).match(/^\s*\(/);
          if (after) cls = "fn";
        }
        push(tokens, m.index, m.index + face.length, cls);
      } else {
        push(tokens, m.index, m.index + face.length, "()[],;{}.".includes(first) ? "punct" : "operator");
      }
    }
    return tokens;
  }

  // llvm: the import entrance format (docs/22). An IR value (%x, @g) is an addressable cell of
  // the IR — the handle register — and the DI metadata (!DIFile, !DILocation, !dbg) is exactly
  // the provenance the import lifts (docs/22): the doc register.
  const LLVM_INSTRUCTIONS = new Set(("add fadd sub fsub mul fmul udiv sdiv fdiv urem srem frem shl lshr ashr and or xor icmp fcmp phi call callbr invoke select freeze alloca load store getelementptr trunc zext sext fptrunc fpext fptoui fptosi uitofp sitofp ptrtoint inttoptr bitcast addrspacecast br ret switch indirectbr resume unreachable va_arg fence cmpxchg atomicrmw extractvalue insertvalue cleanuppad catchpad catchswitch catchret cleanupret").split(" "));
  const LLVM_DIRECTIVES = new Set(("define declare global constant private internal weak common appending dso_local dso_preemptable unnamed_addr local_unnamed_addr external linkonce linkonce_odr weak_odr protected hidden default section comdat align to triple datalayout source_filename module asm attributes uselistorder uselistorder_bb").split(" "));
  const LLVM_TYPES = new Set(("void ptr opaque label metadata token half bfloat float double fp128 x86_fp80 ppc_fp128 x86_mmx").split(" "));

  function lexLlvm(text) {
    const tokens = [];
    const re = new RegExp(
      [
        ";[^\\n]*",                                                     // comment
        "\"[^\"]*\"",                                                   // string
        "![A-Za-z$._0-9]+",                                             // metadata node or attachment
        "[%@](?:\"[^\"]*\"|[-A-Za-z$._0-9]+)",                          // an IR value or global: a cell
        "#[0-9]+",                                                      // an attribute group
        "-?0[xX][0-9a-fA-F]+|-?\\d+(?:\\.\\d*)?(?:[eE][+-]?\\d+)?",     // number
        "[-A-Za-z$._0-9]+",                                             // word
        "[{}()\\[\\],=*<>!:]|\\.\\.\\.",                                // punct
      ].join("|"),
      "g"
    );
    let m;
    while ((m = re.exec(text)) !== null) {
      const face = m[0];
      const first = face[0];
      if (first === ";") push(tokens, m.index, m.index + face.length, "comment");
      else if (first === '"') push(tokens, m.index, m.index + face.length, "string");
      else if (first === "!") {
        const name = face.slice(1);
        const provenance = /^DI[A-Z]/.test(name) || name === "dbg" || /dbg|debug|location|compile_unit|subprogram/.test(name);
        push(tokens, m.index, m.index + face.length, provenance ? "doc" : "meta");
      } else if (first === "%" || first === "@") push(tokens, m.index, m.index + face.length, "handle");
      else if (first === "#") push(tokens, m.index, m.index + face.length, "attr");
      else if (/[0-9-]/.test(first)) push(tokens, m.index, m.index + face.length, "number");
      else if (/[A-Za-z$._]/.test(first)) {
        let cls = null;
        if (/^i\d+$/.test(face) || LLVM_TYPES.has(face)) cls = "type";
        else if (LLVM_INSTRUCTIONS.has(face)) cls = "opcode";
        else if (LLVM_DIRECTIVES.has(face)) cls = "keyword";
        else {
          // A label block sits at the start of a line; `file:` in metadata is a field, not one.
          const before = text.slice(Math.max(0, m.index - 2), m.index);
          if (text[m.index + face.length] === ":" && /(^|[\n;{])\s*$/.test(before)) cls = "path";
        }
        push(tokens, m.index, m.index + face.length, cls);
      } else {
        push(tokens, m.index, m.index + face.length, "()[]{},=*<>:".includes(first) ? "punct" : "operator");
      }
    }
    return tokens;
  }

  // rxfs: the program language tools/native/rxfc.py compiles (the editor's default form). The
  // word groups and the operator tokens are the one meaning table's (tools/highlight/meaning.json,
  // whose check.py marks the same shapes); a declaration names its function, and a call names a
  // cell of the image — those two are addresses.
  const RXFS_KEYWORDS = new Set(("include const program proc macro fn var if else while switch case default break continue return refuse call ret").split(" "));
  const RXFS_DECLS = new Set(["program", "proc", "macro", "fn"]);
  const RXFS_BUILTINS = new Set(("load8 load16 load32 load64 store8 store16 store32 store64 put copy fill").split(" "));

  function lexRxfs(text) {
    const tokens = [];
    const re = new RegExp(
      [
        "#[^\\n]*",                                                     // comment
        "\"(?:\\\\.|[^\"\\\\])*\"",                                     // string
        "0[xX][0-9a-fA-F_]+|[0-9][0-9_]*",                              // number
        "[A-Za-z_][A-Za-z0-9_]*",                                       // word
        "<<|>>|==|!=|<=|>=|[+\\-*/&|<>=!~]",                            // operator (longest first)
        "[(){},;:]",                                                    // punct
      ].join("|"),
      "g"
    );
    let m;
    let decl = false;
    let call = false;
    while ((m = re.exec(text)) !== null) {
      const face = m[0];
      const first = face[0];
      let cls;
      if (first === "#") cls = /TODO|FIXME|XXX/.test(face) ? "doc" : "comment";
      else if (first === '"') cls = "string";
      else if (/[0-9]/.test(first)) cls = "number";
      else if (/[A-Za-z_]/.test(first)) {
        const after = text.slice(m.index + face.length).match(/^\s*\(/);
        if (decl) cls = "fn";
        else if (call) cls = "path"; // `call twice` names a cell of the image
        else if (RXFS_KEYWORDS.has(face)) cls = "keyword";
        else if (RXFS_BUILTINS.has(face)) cls = "builtin";
        else if (after) cls = "fn";
        decl = RXFS_DECLS.has(face);
        call = face === "call";
      } else if ("(){},;:".includes(first)) cls = "punct";
      else cls = "operator";
      push(tokens, m.index, m.index + face.length, cls);
    }
    return tokens;
  }

  // python: the future strands (docs/22, provenance that was once Python).
  const PY_KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case").split(" "));
  const PY_BUILTINS = new Set(("abs all any bool bytes callable chr dict dir divmod enumerate eval float format frozenset getattr hasattr hash help hex id int isinstance issubclass iter len list map max min next oct ord pow print range repr reversed round set setattr sorted str sum super tuple type vars zip open isinstance").split(" "));

  function lexPython(text) {
    const tokens = [];
    const re = new RegExp(
      [
        "#[^\\n]*",                                                     // comment
        "(?:[rRbBuUfF]{0,2})(?:\"\"\"[\\s\\S]*?(?:\"\"\"|$)|'''[\\s\\S]*?(?:'''|$))", // triple string
        "(?:[rRbBuUfF]{0,2})(?:\"(?:\\\\.|[^\"\\\\\\n])*\"?|'(?:\\\\.|[^'\\\\\\n])*'?)", // string
        "@[A-Za-z_][\\w.]*",                                            // decorator
        "0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\\d+(?:\\.\\d*)?(?:[eE][+-]?\\d+)?[jJ]?", // number
        "[A-Za-z_]\\w*",                                                // word
        "\\*\\*|//|:=|[-+*/%@&|^~<>=!,;:()\\[\\]{}.]",                  // operator or punct
      ].join("|"),
      "g"
    );
    let m;
    let afterDef = false;
    while ((m = re.exec(text)) !== null) {
      const face = m[0];
      const first = face[0];
      if (first === "#") push(tokens, m.index, m.index + face.length, /TODO|FIXME|XXX/.test(face) ? "doc" : "comment");
      else if (first === '"' || first === "'" || (face.length > 1 && (face[0] === "r" || face[0] === "R" || face[0] === "b" || face[0] === "B" || face[0] === "f" || face[0] === "F") && /["']/.test(face[1]))) {
        push(tokens, m.index, m.index + face.length, "string");
      } else if (first === "@") push(tokens, m.index, m.index + face.length, "decor");
      else if (/[0-9]/.test(first)) push(tokens, m.index, m.index + face.length, "number");
      else if (/[A-Za-z_]/.test(first)) {
        let cls = null;
        if (afterDef) { cls = face === "class" ? "keyword" : "fn"; afterDef = false; }
        else if (face === "def" || face === "class") { cls = "keyword"; afterDef = true; }
        else if (face === "True" || face === "False" || face === "None") cls = "builtin";
        else if (face === "self" || face === "cls") cls = "builtin";
        else if (PY_KEYWORDS.has(face)) cls = "keyword";
        else if (PY_BUILTINS.has(face)) cls = "builtin";
        push(tokens, m.index, m.index + face.length, cls);
      } else {
        push(tokens, m.index, m.index + face.length, "()[],;:{}.@".includes(first) ? "punct" : "operator");
      }
    }
    return tokens;
  }

  // ---- the language registry: a pane declares its language -------------------------------------
  const LEXERS = {
    text: lexText,
    xml: lexXml,
    rxfd: lexXml,
    rxfs: lexRxfs,
    c: (t) => lexCLike(t, false),
    cpp: (t) => lexCLike(t, true),
    llvm: lexLlvm,
    ll: lexLlvm,
    python: lexPython,
    py: lexPython,
  };

  function tokenize(text, lang) {
    text = String(text);
    const lex = LEXERS[lang];
    return cover(text, lex ? lex(text) : []);
  }

  // ---- from tokens to spans: ONE string per pane, set once --------------------------------------
  function escapeHtml(text) {
    return text.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  }

  function html(text, lang) {
    text = String(text);
    const tokens = tokenize(text, lang);
    if (!tokens.length) return escapeHtml(text);
    const out = [];
    let at = 0;
    for (const token of tokens) {
      if (token.s < at) continue; // overlaps cannot happen, but a boundary slip must not garble text
      out.push(escapeHtml(text.slice(at, token.s)));
      if (token.c === "plain") {
        out.push(escapeHtml(text.slice(token.s, token.e))); // bare: a meaning token's look is its class
      } else {
        out.push('<span class="m-' + token.c + '" title="' + escapeHtml(CLASSES[token.c] || token.c) + '">' + escapeHtml(text.slice(token.s, token.e)) + "</span>");
      }
      at = token.e;
    }
    out.push(escapeHtml(text.slice(at)));
    return out.join("");
  }

  // The editor's source mirror: the base register plus the one meaning table's marks (the
  // green/red layer of tools/highlight/check.py). Marks keep their exact classes and tooltips.
  function decorate(text, lang, marks) {
    text = String(text);
    const base = tokenize(text, lang);
    const seen = (marks || []).slice().sort((a, b) => a.start - b.start);
    const cuts = new Set([0, text.length]);
    for (const t of base) { cuts.add(t.s); cuts.add(t.e); }
    for (const m of seen) {
      if (m.start >= 0 && m.end > m.start) { cuts.add(m.start); cuts.add(m.end); }
    }
    const points = Array.from(cuts).sort((a, b) => a - b);
    const out = [];
    let htmlRun = "";
    let clsRun = "";
    let titleRun = "";
    const flushRun = () => {
      if (clsRun || titleRun) {
        out.push('<span class="' + clsRun.trim() + '"' + (titleRun ? ' title="' + escapeHtml(titleRun) + '"' : "") + ">" + htmlRun + "</span>");
      } else {
        out.push(htmlRun);
      }
      htmlRun = "";
      clsRun = "";
      titleRun = "";
    };
    let bi = 0;
    let mi = 0;
    for (let i = 0; i + 1 < points.length; i += 1) {
      const s = points[i];
      const e = points[i + 1];
      if (e <= s) continue;
      while (bi < base.length && base[bi].e <= s) bi += 1;
      while (mi < seen.length && seen[mi].end <= s) mi += 1;
      const b = base[bi] && base[bi].s <= s && base[bi].e >= e ? base[bi] : null;
      const sem = b && b.c !== "plain" ? b : null; // plain runs carry no meaning and no span
      const m = seen[mi] && seen[mi].start <= s && seen[mi].end >= e ? seen[mi] : null;
      const classes = [];
      if (m) classes.push(m.ok ? "m-ok" : "m-bad", "m-" + m.kind);
      if (sem) classes.push("m-" + sem.c);
      const title = m ? (m.ok ? m.meaning || m.kind : m.refusal || "refused") : (sem ? CLASSES[sem.c] || sem.c : "");
      const piece = escapeHtml(text.slice(s, e));
      const cls = Array.from(new Set(classes)).join(" ");
      if (cls === clsRun && title === titleRun) {
        htmlRun += piece;
      } else {
        flushRun();
        htmlRun = piece;
        clsRun = cls;
        titleRun = title;
      }
    }
    flushRun();
    return out.join("");
  }

  // ---- the DOM side: paint once per content swap, never per token -------------------------------
  function paint(el) {
    if (!el) return;
    const text = el.textContent;
    el.innerHTML = html(text, el.dataset && el.dataset.lang ? el.dataset.lang : "text");
    if (el.dataset) el.dataset.hl = "";
  }

  function paintAll(root) {
    const scope = root && root.querySelectorAll ? root : (typeof document === "undefined" ? null : document);
    if (!scope) return;
    const nodes = scope.querySelectorAll("[data-lang]");
    for (const el of nodes) paint(el);
  }

  function paintText(root) {
    // Prose and mixed markup: walk TEXT nodes and repaint each in place. Already-classified
    // subtrees (a painted pane, an explicit semantic span, a link, a data-nohl region) are
    // left alone; each painted run is wrapped once so repainting is idempotent.
    if (!root) return;
    const skip = (el) => el.closest("[data-hl],[data-nohl]") || (el.closest("a[data-path]") !== null) || Array.from(el.classList || []).some((c) => c.indexOf("m-") === 0);
    const visit = (node) => {
      for (const child of Array.from(node.childNodes)) {
        if (child.nodeType === 3) {
          const text = child.nodeValue;
          if (!text || !text.trim()) continue;
          const wrapper = document.createElement("span");
          wrapper.dataset.hl = "";
          wrapper.innerHTML = html(text, "text");
          child.replaceWith(wrapper);
        } else if (child.nodeType === 1) {
          if (skip(child)) continue;
          if (child.tagName === "SCRIPT" || child.tagName === "STYLE") continue;
          visit(child);
        }
      }
    };
    if (root.nodeType === 3) return;
    if (root.nodeType === 1 && skip(root)) return;
    visit(root);
  }

  function refresh(root) {
    paintAll(root);
    paintText(root);
  }

  // No flash of uncolored text: every declared pane paints on load, and pages repaint after
  // their content swaps (renderDelta, renderFace, renderDetail call refresh).
  if (typeof document !== "undefined") {
    const boot = () => paintAll(document);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }

  // ---- the unit-test surface (tests/test_server_views.py drives it through node) ---------------
  // One fixed battery of classification facts over the pure functions above: the expectations
  // live with the tests, the samples live with the register, and nothing here touches the DOM.
  function selftest() {
    const values = {};
    for (const value of ["RefKind.Data", "RefKind.Doc", "@kinds.Effects.Write|Allocate", "Form.Struct", "DocRole.Narrates", "DocKind.Todo", "Opcode.MoveSlot", "FunctionLayer.Library", "pymergetic.rxf.roles.call.callee", "old.thing", "42", "0x2a", "true", "MODULE", "a"]) {
      values[value] = classifyValue(value);
    }
    const words = {};
    for (const word of ["MODULE", "Module", "Pinned", "Call", "Write", "Todo", "NothingLikeThis"]) {
      words[word] = classifyWord(word);
    }
    const samples = {
      xml: "<rxf:doc name=\"intro\" role=\"DocRole.Narrates\">was once Python</rxf:doc>",
      rxfs: "program double(x) = mul(x, 2)  # TODO: fold\nproc twice { x = x + x; call twice; }",
      llvm: "define i32 @f(i32 %x) {\nentry:\n  ret i32 %x\n}",
      c: "uint64_t f(void) { return 0; } // a comment",
      python: "def f(x): return len(x)  # TODO",
      text: "refused: target \"nowhere\" #42 and pymergetic.rxf.selfedit.triple",
    };
    const tokens = {};
    for (const lang of Object.keys(samples)) {
      tokens[lang] = tokenize(samples[lang], lang).map((t) => [samples[lang].slice(t.s, t.e), t.c]);
    }
    return { values: values, words: words, samples: samples, tokens: tokens };
  }

  return {
    REGISTER: {
      languages: Object.keys(LEXERS),
      classes: CLASSES,
      enums: ENUMS,
    },
    tokenize: tokenize,
    classifyWord: classifyWord,
    classifyValue: classifyValue,
    selftest: selftest,
    html: html,
    inline: html,
    decorate: decorate,
    paint: paint,
    paintText: paintText,
    paintAll: paintAll,
    refresh: refresh,
  };
});
