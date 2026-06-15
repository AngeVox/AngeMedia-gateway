import { api } from '../../api.js';
import { assetDisplayName, isImageAsset, safeAssetHref } from '../../lib/asset-url.js';

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

export function imageReferenceAssets(assets = []) {
  return assets
    .filter(isImageAsset)
    .map((asset) => {
      const value = safeAssetHref(asset.url_path);
      if (!value) return null;
      return {
        id: asset.id || value,
        value,
        label: assetDisplayName(asset),
        source: asset.source || '',
      };
    })
    .filter(Boolean);
}

export async function loadImageReferenceAssets() {
  const result = await api.get('/assets?limit=100&offset=0');
  return imageReferenceAssets(dataArray(result));
}
