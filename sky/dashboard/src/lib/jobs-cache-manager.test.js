import dashboardCache from './cache';
import { getManagedJobs } from '@/data/connectors/jobs';
import { JobsCacheManager } from './jobs-cache-manager';

jest.mock('./cache', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    invalidateFunction: jest.fn(),
  },
}));

jest.mock('@/data/connectors/jobs', () => ({
  getManagedJobs: jest.fn(),
}));

describe('JobsCacheManager', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    delete window.__skyJobsPaginationFetch;
  });

  afterEach(() => {
    delete window.__skyJobsPaginationFetch;
  });

  test('default path fetches only the requested page', async () => {
    dashboardCache.get.mockResolvedValue({
      jobs: [{ id: 20 }],
      total: 50,
      totalNoFilter: 50,
      statusCounts: { RUNNING: 1 },
    });
    const manager = new JobsCacheManager();

    const result = await manager.getPaginatedJobs({
      allUsers: true,
      page: 2,
      limit: 10,
    });

    expect(result.jobs).toEqual([{ id: 20 }]);
    expect(dashboardCache.get).toHaveBeenCalledTimes(1);
    expect(dashboardCache.get).toHaveBeenCalledWith(getManagedJobs, [
      { allUsers: true, page: 2, limit: 10 },
    ]);
  });

  test('default path reuses the cached page', async () => {
    dashboardCache.get.mockResolvedValue({ jobs: [{ id: 1 }], total: 1 });
    const manager = new JobsCacheManager();
    const options = { page: 1, limit: 10 };

    await manager.getPaginatedJobs(options);
    const result = await manager.getPaginatedJobs(options);

    expect(result.fromCache).toBe(true);
    expect(dashboardCache.get).toHaveBeenCalledTimes(1);
  });

  test('plugin path remains independent of the default connector', async () => {
    window.__skyJobsPaginationFetch = jest.fn().mockResolvedValue({
      items: [{ id: 1 }],
      total: 1,
    });
    const manager = new JobsCacheManager();

    const result = await manager.getPaginatedJobs({ page: 1, limit: 10 });

    expect(result.jobs).toEqual([{ id: 1 }]);
    expect(window.__skyJobsPaginationFetch).toHaveBeenCalledTimes(1);
    expect(dashboardCache.get).not.toHaveBeenCalled();
  });
});
