// 按域聚合 API，供业务层统一从 @/api 引用。
import { auditApi } from './api_audit'
import { authApi } from './api_auth'
import { issuesApi } from './api_issues'
import { libraryApi } from './api_library'
import { qaApi } from './api_qa'
import { settingsApi } from './api_settings'

export const api = {
  ...libraryApi,
  ...settingsApi,
  ...qaApi,
  ...auditApi,
  ...issuesApi,
  ...authApi,
}
