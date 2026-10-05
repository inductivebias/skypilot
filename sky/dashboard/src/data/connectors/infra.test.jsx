import { apiClient } from '@/data/connectors/client';
import { getContextGPUData } from '@/data/connectors/infra';

jest.mock('@/data/connectors/client', () => ({
  apiClient: {
    post: jest.fn(),
    get: jest.fn(),
  },
}));

describe('getContextGPUData', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('derives CKS GPU bounds from its exact NodePool snapshot', async () => {
    apiClient.post.mockResolvedValue({
      ok: true,
      headers: { get: () => 'request-id' },
    });
    apiClient.get.mockResolvedValue({
      ok: true,
      json: async () => ({
        return_value: JSON.stringify({
          node_info_dict: Object.fromEntries(
            Array.from({ length: 8 }, (_, index) => [
              `node-${index}`,
              {
                name: `node-${index}`,
                accelerator_type: 'H100',
                total: { accelerator_count: 8 },
                free: { accelerators_available: 0 },
              },
            ])
          ),
          custom_resource: {
            metadata: { name: 'flourish-h100-spot' },
            spec: { minNodes: 8, maxNodes: 20 },
          },
        }),
      }),
    });

    const result = await getContextGPUData('cks-use06a');

    expect(apiClient.post).toHaveBeenCalledWith('/kubernetes_node_info', {
      context: 'cks-use06a',
      custom_resource: {
        group: 'compute.coreweave.com',
        version: 'v1alpha1',
        plural: 'nodepools',
        name: 'flourish-h100-spot',
      },
    });
    expect(result.perContextGPUs).toEqual([
      expect.objectContaining({
        gpu_name: 'H100',
        gpu_total: 64,
        gpu_min: 64,
        gpu_max: 160,
      }),
    ]);
  });
});
