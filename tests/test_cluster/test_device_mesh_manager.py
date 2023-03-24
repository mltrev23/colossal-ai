from functools import partial

import torch
import torch.multiprocessing as mp

from colossalai.cluster.device_mesh_manager import DeviceMeshInfo, DeviceMeshManager
from colossalai.device.device_mesh import DeviceMesh
from colossalai.fx.tracer import ColoTracer
from colossalai.initialize import launch
from colossalai.logging import disable_existing_loggers
from colossalai.utils import free_port


def check_device_mesh_manager(rank, world_size, port):
    disable_existing_loggers()
    launch(config={}, rank=rank, world_size=world_size, host='localhost', port=port, backend='nccl')
    device_mesh_manager = DeviceMeshManager()
    device_mesh_info_auto = DeviceMeshInfo(physical_ids=[0, 1, 2, 3],)
    device_mesh_auto = device_mesh_manager.create_device_mesh('0', device_mesh_info_auto)
    assert device_mesh_auto.shape == (2, 2)
    assert device_mesh_auto._logical_mesh_id.tolist() == [[0, 1], [2, 3]]

    device_mesh_info_with_shape = DeviceMeshInfo(
        physical_ids=[0, 1, 2, 3],
        mesh_shape=(2, 2),
    )
    device_mesh_with_shape = device_mesh_manager.create_device_mesh('1', device_mesh_info_with_shape)

    assert device_mesh_with_shape.shape == (2, 2)
    assert device_mesh_with_shape._logical_mesh_id.tolist() == [[0, 1], [2, 3]]


def test_device_mesh_manager():
    world_size = 4
    run_func = partial(check_device_mesh_manager, world_size=world_size, port=free_port())
    mp.spawn(run_func, nprocs=world_size)


if __name__ == '__main__':
    test_device_mesh_manager()
