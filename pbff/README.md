# bpff folder structure

```
.
└── pbff/
    └── <PBFF font name>/
        ├── 14_bold.pbff
        ├── 14.pbff
        ├── 18_bold.pbff
        ├── 18.pbff
        ├── 24_bold.pbff
        ├── 24.pbff
        ├── 28_bold.pbff
        └── 28.pbff
```

PBFF font group folder must contain all sizes with bold variants as listed in above example, but not all PBFF files must contain every needed characters.

Each PBFF file must contain `▯` (decimal codepoint 9647) wildcard character as a first glyph. There is a **wildcard** template PBFF font group to copy needed glyphs for convenience or to be used as a base for new font groups.
