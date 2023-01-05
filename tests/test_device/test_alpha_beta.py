from functools import partial

import pytest
import torch.multiprocessing as mp

from colossalai.device import AlphaBetaProfiler
from colossalai.initialize import launch
from colossalai.logging import disable_existing_loggers
from colossalai.testing import parameterize, rerun_if_address_is_in_use
from colossalai.utils import free_port


def check_alpha_beta(rank, physical_devices, world_size, port):
    disable_existing_loggers()
    launch(config={}, rank=rank, world_size=world_size, host='localhost', port=port, backend='nccl')
    profiler = AlphaBetaProfiler(physical_devices)
    ab_dict = profiler.profile_ab()
    for _, (alpha, beta) in ab_dict.items():
        assert alpha > 0 and alpha < 1e-4 and beta > 0 and beta < 1e-10


@pytest.mark.skip(reason="Skip because assertion fails for CI devices")
@pytest.mark.dist
@parameterize('physical_devices', [[0, 1, 2, 3], [0, 3]])
@rerun_if_address_is_in_use()
def test_profile_alpha_beta(physical_devices):
    world_size = 4
    run_func = partial(check_alpha_beta, physical_devices=physical_devices, world_size=world_size, port=free_port())
    mp.spawn(run_func, nprocs=world_size)


if __name__ == '__main__':
    test_profile_alpha_beta()
