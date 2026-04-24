export interface UsersGridItem {
  username: string;
  full_name: string;
  avatar_url: string;
}

export interface UsersPagination {
  count: number;
  page: number;
  num_pages: number;
  page_numbers: number[];
  show_first: boolean;
  show_last: boolean;
  has_previous: boolean;
  has_next: boolean;
  previous_page_number: number | null;
  next_page_number: number | null;
  start_index: number;
  end_index: number;
}

export interface UsersGridResponse {
  users: UsersGridItem[];
  pagination: UsersPagination;
}

export interface UsersBootstrap {
  usersApiUrl: string;
}

export function readUsersBootstrap(root: HTMLElement): UsersBootstrap | null {
  const usersApiUrl = String(root.dataset.usersApiUrl || "").trim();
  if (!usersApiUrl) {
    return null;
  }

  return {
    usersApiUrl,
  };
}
