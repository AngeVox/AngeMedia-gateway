import { getTextToImageOperation } from './operation-capabilities.js';

export const MODELSCOPE_PROVIDER_ID = 'modelscope';
const MAX_MODELSCOPE_OPTIONS = 6;

function hasCurrentDirectory(directory, now) {
  return directory?.status === 'success'
    && directory.evidence_state === 'current'
    && directory.model_list_validated === true
    && Number.isFinite(directory.deadline)
    && now < directory.deadline
    && Array.isArray(directory.project_model_visibility?.listed);
}

export function filterAccountImageModels(models, directory, now = Date.now()) {
  const listed = new Set(hasCurrentDirectory(directory, now) ? directory.project_model_visibility.listed : []);
  const first = models.findIndex(model => model.provider_id === MODELSCOPE_PROVIDER_ID);
  if (first < 0) return models;
  const unique = new Map();
  for (const model of models) {
    if (model.provider_id === MODELSCOPE_PROVIDER_ID && !unique.has(model.provider_model)) {
      unique.set(model.provider_model, model);
    }
  }
  // 账号列表只为已适配的型号排序。实测可调用的文生图型号可能未被列表返回。
  const rank = model => (getTextToImageOperation(model) ? 0 : 3)
    + (listed.has(model.provider_model) ? 0 : model.id === 'z-turbo' ? 1 : 2);
  const options = [...unique.values()].sort((a, b) => rank(a) - rank(b)).slice(0, MAX_MODELSCOPE_OPTIONS);
  return models.flatMap((model, index) => index === first ? options
    : model.provider_id === MODELSCOPE_PROVIDER_ID ? [] : [model]);
}

export function modelScopeDirectoryMessage(directory, hasModels, now = Date.now()) {
  if (hasCurrentDirectory(directory, now)) {
    return hasModels && directory.project_model_visibility.listed.length
      ? 'generateImage.modelScopeAvailable' : 'generateImage.modelScopeEmpty';
  }
  if (directory?.status === 'unsupported') return 'providers.connectionModelScopeOfficialOnly';
  return directory?.status === 'failed'
    ? 'generateImage.modelScopeRefreshFailed' : 'generateImage.modelScopeRefreshNeeded';
}
