// 上传策略（`app/service/upload_policy.py` 是服务端的唯一权威）。
//
// 前端要在本地做即时 UX：`accept` 属性、剩余份数、超出提示、粘贴分类。这些**不**
// 自己记一份 —— 此前 `MAX_UPLOAD_FILES = 20` 在 `Upload.jsx` 里写死三处、受理
// 扩展名另有一套，运维者调大配置前端不会跟着变。策略由服务端下发，这里只在拿不到
// 时回落到内建默认值。
//
// **回落不是「策略可选」**：一次请求失败不该挡住患者上传。默认值只是此刻的起点，
// 拿到服务端那份之后一律以它为准。

import { getUploadPolicy } from './api.js';

export const DEFAULT_UPLOAD_POLICY = Object.freeze({
  accepted_extensions: Object.freeze(['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp']),
  max_files: 20,
  max_file_bytes: 20 * 1024 * 1024,
  max_total_bytes: 50 * 1024 * 1024,
});

// 归一化：只接受已知的键，缺项用内建默认补齐。
// 服务端多下发一个键不代表前端该认识它；少下发一个也不该让界面拿到 undefined。
export function normalizeUploadPolicy(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const extensions = Array.isArray(source.accepted_extensions)
    ? source.accepted_extensions.filter((item) => typeof item === 'string' && item.startsWith('.'))
    : [];
  return {
    accepted_extensions: extensions.length > 0 ? extensions : DEFAULT_UPLOAD_POLICY.accepted_extensions,
    max_files: positiveInt(source.max_files, DEFAULT_UPLOAD_POLICY.max_files),
    max_file_bytes: positiveInt(source.max_file_bytes, DEFAULT_UPLOAD_POLICY.max_file_bytes),
    max_total_bytes: positiveInt(source.max_total_bytes, DEFAULT_UPLOAD_POLICY.max_total_bytes),
  };
}

function positiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

export async function loadUploadPolicy() {
  try {
    return normalizeUploadPolicy(await getUploadPolicy());
  } catch {
    return normalizeUploadPolicy(null);
  }
}

/** `accept` 属性要用逗号分隔的扩展名清单。空清单不是「什么都不受理」，回落默认。 */
export function acceptAttribute(policy) {
  return normalizeUploadPolicy(policy).accepted_extensions.join(',');
}

/** 给患者看的体积（「20MB」/「1.5MB」）；只用于提示文案，不作为判据。 */
export function describeBytes(bytes) {
  const value = positiveInt(bytes, DEFAULT_UPLOAD_POLICY.max_file_bytes);
  const megabytes = value / (1024 * 1024);
  return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)}MB`;
}
