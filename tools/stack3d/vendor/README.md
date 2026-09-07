# vendor/

Third-party code committed into the repo so the viewer works with **no network
access at all**. An air-gapped or firewalled environment cannot reach cdnjs, so
`build.py` inlines `three.min.js` directly into the generated HTML.

| file | what | version | license |
|---|---|---|---|
| `three.min.js` | three.js UMD build (defines the `THREE` global) | r128 | MIT — see `LICENSE.three` |

Fetched from the npm registry, not a CDN:

```bash
curl -sL https://registry.npmjs.org/three/-/three-0.128.0.tgz -o three.tgz
tar xzf three.tgz package/build/three.min.js package/LICENSE
```

r128 is pinned deliberately: it is the last widely-mirrored revision shipping a
UMD build that defines a global. Later revisions are ES-module only, which
would need an import map or a bundler — neither is worth it for one page.

## Fonts

Fonts are NOT vendored. A CJK webfont family is several megabytes, which is a
poor trade for a page whose typography degrades gracefully. `head.html` keeps
the Google Fonts `<link>` (used when the network allows it) and names real
system CJK faces in every fallback stack, so an offline machine renders in
PingFang / Microsoft JhengHei / Noto Sans CJK instead of a fallback that cannot
draw Chinese.
