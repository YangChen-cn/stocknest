# Watercolor garden assets

Original illustrations generated with the built-in ImageGen tool, then composed
offline with Pillow. No real portfolio, personal labels, or uploaded references
were used. The first botanical atlas was used solely as a style reference for
its replacement; only the replacement is shipped.

- `plants.png`: six foliage families, five healthy growth stages (1024 × 1536).
- `flowers.png`: peony, camellia, chrysanthemum, iris, daffodil, lotus; five stages
  from sprout to natural flowering (1024 × 1536).
- `ornaments.png`: sun, rain, clouds, flowers, watering can, scissors, specimen
  card and autumn sprig (1776 × 888).

All sources have alpha transparency. Sprite boundaries are registered in
`garden_render.py`; they accommodate the actual generated layout. Species are
decorative, never an assessment of the corresponding investment. Natural
flowering follows holding age; separate added flower ornaments commemorate only
verified closing portfolio NAV highs.

## Final prompts

### Foliage (edit of our own initial generated atlas)

Re-layout this watercolor plant atlas while preserving its painting style and all six botanical families and five healthy growth stages. Output portrait 1024x1536 with EXACT 5 columns and 6 rows. Each cell is 204.8px wide and 256px high. Center each separate potted plant inside its corresponding cell. Absolutely mandatory: ALL foliage, pots and their alpha pixels must stay in the central 150px wide by 190px high area of each cell. This leaves large TRANSPARENT gutters all around EVERY sprite. No object touches any neighboring cell or plant, especially mature right-column plants. Each sprite is a standalone cutout, NO colored background, no text, no labels, no grid lines. Row1 herb, row2 fern, row3 rosemary, row4 olive tree, row5 hydrangea foliage without flowers, row6 bamboo. Column1 tiny sprout, column2 seedling, column3 spreading young plant, column4 lush plant, column5 mature leafy plant. All healthy, distinct growth sizes. Strong watercolor texture, muted sage and terracotta. This is a technical atlas for deterministic cropping at equal grid boundaries, so obey regular cell placement and very generous clear gaps.

### Flowers

Production watercolor FLOWERING plant sprite atlas for a quiet private garden journal, authentic delicate botanical illustration with pigment texture, warm terracotta and cream pots. PORTRAIT 1024x1536. Exactly FIVE columns and SIX rows of isolated objects with large TRANSPARENT gutters. All pixels of each separate object must remain inside its grid cell, no overlap, no backdrop, no text, no labels, no grid lines, true alpha. Equal 204px-wide, 256px-high cells, each object fits within central 155px-wide by 185px-high area. Rows top to bottom are: 1 pink peony, 2 red-pink camellia, 3 soft golden chrysanthemum, 4 violet iris, 5 cream and butter-yellow daffodil, 6 pink lotus in a shallow ceramic water bowl. Columns left-to-right show five visibly distinct healthy growth stages: tiny sprout with two leaves, small leafy seedling without blossoms, spreading plant with tiny unopened buds, lush plant with one opening bloom, mature beautiful flowering plant with multiple blooms. Each flowering family must be visually recognizable in mature columns. All stages potted; every pot base aligned within its cell. Keep plants healthy. Natural leaf growth smaller to larger. Quiet comforting hand-painted watercolor, sage greens, dusty rose, muted lilac and ochre, editorial botanical plate, tasteful generous empty spacing. No green background, no paper backing. Six rows exactly, five columns exactly.

### Ornaments

Create a transparent PNG sprite sheet of watercolor garden ornaments, one clean isolated illustration per cell in a precise 4-column by 2-row grid, generous clear alpha gutters, NO background, NO letters, NO words, NO numbers, NO logo, all objects fully within each cell. Row 1 left to right: small honey-gold sun with a soft halo; a gentle soft blue-grey rain cloud with fine raindrops; two pale peaceful clouds; a little loose cluster of coral and cream wildflowers with leaves. Row 2 left to right: delicate sage-green watering can; antique small pruning scissors; blank cream paper botanical specimen card with a pressed leaf and no writing; a tiny warm golden leaf sprig with berries for an autumn garden corner. Handmade watercolor pigment texture, restrained soft edges, warm cream and muted green palette, botanical journal editorial illustration, matches watercolor potted plants, comforting and quiet. Each icon should have approximately equal visual weight, centered in its cell. True transparent background, no painted backdrop.

## Literature sources

The 24 short extracts, original-language credits, source URLs and our own reading
notes are kept in `stockwatch/i18n.py`. Chinese texts follow Wikisource originals;
English texts follow Project Gutenberg's historical Wordsworth (1807), Blake and
Dickinson editions. Dickinson's edited early texts and their editorial titles are
used as printed, rather than silently substituting a modern edition.
