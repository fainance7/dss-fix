export interface UserRoleOut {
  role_name: string
  region_id: string | null
  region_name: string | null
  assigned_at: string
}

export interface UserDirectionOut {
  id: string
  code: string
  name: string
}

export interface UserOut {
  id: string
  username: string
  email: string
  iin: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  roles: UserRoleOut[]
  directions: UserDirectionOut[]
}

export interface UserCreate {
  username: string
  email: string
  password: string
  is_active?: boolean
  iin?: string
}

export interface UserUpdate {
  username?: string
  email?: string
  is_active?: boolean
  password?: string
  iin?: string
}

export interface UserListResponse {
  items: UserOut[]
  total: number
  page: number
  page_size: number
  pages: number
}
