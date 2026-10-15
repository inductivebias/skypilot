import { DASHBOARD_CACHE_FUNCTIONS } from './cache-preloader';

test('jobs page preloader excludes managed jobs fetched by the table', () => {
  expect(DASHBOARD_CACHE_FUNCTIONS.pages.jobs).toEqual([
    'getClusters',
    'getWorkspaces',
    'getUsers',
  ]);
});
