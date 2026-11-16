//FL:START
// Exercise the real cache and connector through the external HTTP boundary.
// A dashboard refresh must not download finished history or every job's YAML.
jest.mock('@/data/connectors/client', () => ({
  apiClient: { get: jest.fn(), post: jest.fn() },
}));

import { apiClient } from '@/data/connectors/client';
import { getManagedJobs } from '@/data/connectors/jobs';
import dashboardCache from './cache';
import cachePreloader from './cache-preloader';
import jobsCacheManager from './jobs-cache-manager';

beforeEach(() => {
  jest.clearAllMocks();
  dashboardCache.clear();
  jobsCacheManager.invalidateCache();
  delete window.__skyJobsPaginationFetch;
  apiClient.post.mockResolvedValue({
    ok: true,
    headers: new Headers({ 'X-Skypilot-Request-ID': 'page-request' }),
  });
  apiClient.get.mockResolvedValue({
    ok: true,
    json: async () => ({
      return_value: JSON.stringify({ jobs: [], total: 10000 }),
    }),
  });
});

test('navigation and refresh fetch bounded pages without background history', async () => {
  const options = {
    allUsers: true,
    skipFinished: true,
    page: 1,
    limit: 10,
    sortBy: 'submitted_at',
    sortOrder: 'asc',
  };
  await jobsCacheManager.getPaginatedJobs(options);
  await jobsCacheManager.getPaginatedJobs({ ...options, page: 2 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(apiClient.post).toHaveBeenCalledTimes(2);
  for (const [index, [path, body]] of apiClient.post.mock.calls.entries()) {
    expect(path).toBe('/jobs/queue/v2');
    expect(body).toMatchObject({
      page: index + 1,
      limit: 10,
      skip_finished: true,
      sort_by: 'submitted_at',
      sort_order: 'asc',
    });
    expect(body.fields).not.toContain('user_yaml');
  }
  jobsCacheManager.invalidateCache();
  await jobsCacheManager.getPaginatedJobs(options);
  expect(apiClient.post).toHaveBeenCalledTimes(3);
  expect(apiClient.post.mock.calls[2][1].limit).toBe(10);
});

test('explicit history has a separate cache and details still request full YAML', async () => {
  await jobsCacheManager.getPaginatedJobs({
    page: 1,
    limit: 10,
    skipFinished: true,
  });
  await jobsCacheManager.getPaginatedJobs({
    page: 1,
    limit: 10,
    skipFinished: false,
  });
  expect(apiClient.post).toHaveBeenCalledTimes(2);
  expect(apiClient.post.mock.calls[1][1]).toMatchObject({
    skip_finished: false,
    limit: 10,
  });
  await getManagedJobs({ allFields: true, jobIDs: ['123'] });
  const detail = apiClient.post.mock.calls[2][1];
  expect(detail.job_ids).toEqual(['123']);
  expect(detail.fields).toBeUndefined();
});

test.each(['jobs', 'clusters', 'workspaces', 'users', 'base'])(
  '%s preloading never fetches job lists',
  async (page) => {
    if (page === 'base') {
      await cachePreloader.preloadBaseFunctions(true);
    } else {
      await cachePreloader.preloadForPage(page, { force: true });
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(
      apiClient.post.mock.calls.filter(([path]) => path === '/jobs/queue/v2')
    ).toHaveLength(0);
  }
);
//FL:END
