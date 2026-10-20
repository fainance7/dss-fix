export interface RoleInfo {
  name: string
  region_id: string | null
  region_name: string | null
}

export interface DirectionInfo {
  id: string
  code: string
  name: string
}

export interface MeResponse {
  id: string
  username: string
  email: string
  is_active: boolean
  roles: RoleInfo[]
  directions: DirectionInfo[]
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export type RoleName =
  | 'admin'
  | 'exp_ver'
  | 'exp_dir'
  | 'akim_obl'
  | 'akim_ray'
  | 'data_uploader'

export interface ECPChallengeResponse {
  challenge_token: string
  nonce: string
}

export interface ECPLoginRequest {
  challenge_token: string
  cms_signature: string
}

export interface NCALayerResponse {
  responseObject: string | null
  message: string | null
  errorCode: string
}
