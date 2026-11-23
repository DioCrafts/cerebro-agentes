// ESLint rule `ui/solo-tokens`: migrated files use the design system (docs/ui/sistema-de-diseno.md).
//
// Reported: Tailwind palette colours (bg-red-500, text-white…), the app's legacy colour tokens (bg-paper,
// text-ink, border-line…), literal colours in arbitrary values (bg-[#fff], text-[rgb(…)]), the legacy
// shadows (shadow-card, shadow-pop), font-serif, arbitrary font sizes (text-[13.5px]) and imports of
// lucide-react (icons are Octicons, from src/ui/iconos.ts).
//
// A file not yet migrated says so with the comment `/* ui-legado */` (any line): the rule skips it. Migrating
// a file = removing that comment and fixing what the rule then reports.

const PALETA =
  'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const LEGADO =
  'paper|surface|ink|ink-soft|muted|line|primary|primary-hover|primary-dark|primary-soft|navy|grounding-[a-z-]+|risk-[a-z-]+'
const PREFIJOS =
  'bg|text|border(?:-[trblxyse])?|ring|ring-offset|outline|fill|stroke|from|via|to|divide|placeholder|decoration|accent|caret|shadow'

const COLOR = new RegExp(
  `(?:^|[\\s:!"'\`])!?(?:${PREFIJOS})-(?:(?:${PALETA})-\\d{2,3}|black|white|(?:${LEGADO})(?=[\\s/!"'\`]|$)|\\[(?:#|rgb|hsl|oklch|color|var\\(--(?:paper|surface|ink|muted|line|primary|navy)))`,
)
const OTROS = /(?:^|[\s:!"'`])(?:shadow-(?:card|pop)|font-serif|text-\[\d)/

const MARCA = /^\s*ui-legado\b/

export const soloTokens = {
  meta: {
    type: 'problem',
    docs: { description: 'Colours, type and icons only from the design system (src/ui)' },
    messages: {
      clase:
        'Clase fuera del sistema de diseño: «{{clase}}». Usa los tokens de Primer (text-fg-muted, bg-canvas-muted, border-border-default…) o un componente de src/ui. Ver docs/ui/sistema-de-diseno.md.',
      lucide: 'Los iconos son Octicons: impórtalos de src/ui/iconos.ts, no de lucide-react.',
    },
    schema: [],
  },
  create(context) {
    const comentarios = context.sourceCode.getAllComments()
    if (comentarios.some((c) => MARCA.test(c.value))) return {}
    function revisar(node, texto) {
      const m = COLOR.exec(texto) ?? OTROS.exec(texto)
      if (m) context.report({ node, messageId: 'clase', data: { clase: m[0].trim().replace(/^["'`]/, '') } })
    }
    return {
      Literal(node) {
        if (typeof node.value === 'string') revisar(node, node.value)
      },
      TemplateElement(node) {
        revisar(node, node.value.raw)
      },
      ImportDeclaration(node) {
        if (node.source.value === 'lucide-react') context.report({ node, messageId: 'lucide' })
      },
    }
  },
}

export default { rules: { 'solo-tokens': soloTokens } }
