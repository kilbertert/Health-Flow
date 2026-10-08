// 粘贴内容的分类与命名 —— **唯一**一处回答「这份剪贴内容受不受理、它叫什么」。
//
// 受理判据只有一条：扩展名在服务端下发的受理集合里（`uploadPolicy.js`）。此前
// 分两处各答一次：`pasteMimeFromFileName` 认得 webp/heic，而
// `UNSUPPORTED_PASTE_IMAGE_TYPES` 又在入口把同样的类型拒掉 —— 同一份剪贴内容在
// 两个函数里得到相反态度。现在两个问题都由分类函数回答，它们不可能再分叉。
//
// 命名同样只在这里决定：**有源文件名就保留它**（词条「报告原始材料」承诺保留
// 文件名，此前粘贴路径把它换成了 `粘贴-<时间戳>`，同一份材料走「选择文件」与走
// 「粘贴」得到不同的 `original_filename`）。没有源文件名的剪贴板 blob（items /
// data:image / HTML 三条路径都拿不到名字）才生成一个 —— 生成名带序号与时间，
// 是可回溯的先后顺序，而不是空名。

/** MIME → 扩展名。只覆盖受理集合里的图片类型；其余一律交给受理集合去判。 */
export const MIME_EXTENSIONS = Object.freeze({
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/bmp': 'bmp',
});

/** 扩展名 → MIME。生成名与上传都要一个具体的类型。 */
export const EXTENSION_MIMES = Object.freeze({
  png: 'image/png',
  jpg: 'image/jpeg',
  gif: 'image/gif',
  bmp: 'image/bmp',
  pdf: 'application/pdf',
});

export const DATA_IMAGE_RE = /data:image\/([a-z0-9.+-]+);base64,([a-z0-9+/=]+)/gi;

/**
 * 从 `text/html` 剪贴内容里取出内嵌的 base64 图片。
 *
 * 正则捕获的是**子类型**（`png`），这里补回 `image/` 前缀 —— 受理判据看的是完整
 * 类型串，单独一个 `png` 在它眼里什么都不是。此前这条路径靠「不认识的类型默认
 * 落到 png」蒙对，收敛之后没有那个兜底了。
 */
export function dataImageCandidates(html) {
  const found = [];
  // 共享的正则带 `g`，`lastIndex` 是模块级状态；这里每次都从零开始找，即使别的
  // 调用点（例如一次 `exec`）把它留在了中间位置。
  DATA_IMAGE_RE.lastIndex = 0;
  for (const match of String(html || '').matchAll(DATA_IMAGE_RE)) {
    found.push({ mime: `image/${normalizePasteMime(match[1])}`, base64: match[2] });
  }
  return found;
}

export function normalizePasteMime(value) {
  return String(value || '').split(';', 1)[0].trim().toLowerCase();
}

/** 从文件名取扩展名（小写、无点）；取不到是空串。 */
export function extensionFromName(name) {
  const text = String(name || '');
  const dot = text.lastIndexOf('.');
  if (dot <= 0 || dot === text.length - 1) return '';
  return text.slice(dot + 1).toLowerCase();
}

/**
 * `image/<x>` → `<x>`。**不查受理集合** —— 它只把 MIME 的字面类型取出来，
 * 受理与否由调用方那一个判据回答。webp/heic 走到这里仍然得 `webp`/`heic`，
 * 于是提示能说清是什么格式不被受理，而不是笼统的「暂不支持」。
 */
export function extensionFromMime(mime) {
  const text = normalizePasteMime(mime);
  if (!text.startsWith('image/')) return '';
  const subtype = text.slice('image/'.length);
  if (subtype === 'jpeg' || subtype === 'jpg') return 'jpg';
  return /^[a-z0-9]+$/.test(subtype) ? subtype : '';
}

/** 受理集合归一为**裸扩展名**的集合（服务端下发的是带点的形式）。 */
export function acceptedExtensionSet(acceptedExtensions) {
  return new Set(
    (acceptedExtensions || [])
      .map((item) => String(item).replace(/^\./, '').toLowerCase())
      .filter(Boolean),
  );
}

/**
 * 分类一份剪贴内容。
 *
 * 判据是**扩展名**，而扩展名优先取内容自己的 MIME（`image/png` 比文件名可信），
 * 取不到才看文件名。这正是「入口与分类结论一致」的落点：两个问题共用一个判据，
 * 不可能一边认得 webp 一边拒掉它。
 */
export function classifyPaste(candidate, acceptedExtensions) {
  const accepted = acceptedExtensionSet(acceptedExtensions);
  const mime = normalizePasteMime(candidate?.mime);
  const sourceName = String(candidate?.name || '');
  const extension = MIME_EXTENSIONS[mime] || extensionFromMime(mime) || extensionFromName(sourceName);
  if (!extension) {
    // 既没有可识别的 MIME，也没有可用的后缀 —— 不知道它是什么，不猜。
    return { supported: false, extension: '', mime, sourceName };
  }
  if (!accepted.has(extension)) {
    return { supported: false, extension, mime, sourceName };
  }
  return {
    supported: true,
    extension,
    mime: EXTENSION_MIMES[extension] || mime || `image/${extension}`,
    sourceName,
  };
}

/** 待上传项的文件名：**有源名就用源名**，没有才生成一个可回溯的。 */
export function pastedFileName(candidate, { now, sequence } = {}) {
  if (candidate?.sourceName) return candidate.sourceName;
  const stamp = now === undefined ? Date.now() : now;
  return `粘贴-${stamp}-${sequence}.${candidate?.extension || 'png'}`;
}

/** 一句给患者看的「不受理」提示 —— 说清是什么类型不被受理，而不是笼统的「暂不支持」。 */
export function unsupportedPasteMessage(candidate, acceptedExtensions) {
  const extension = (candidate?.extension || '').toUpperCase();
  const accepted = [...acceptedExtensionSet(acceptedExtensions)].map((item) => item.toUpperCase());
  if (!extension) return '暂不支持';
  return `暂不支持 ${extension} 格式（支持 ${accepted.join(' / ')}）`;
}
