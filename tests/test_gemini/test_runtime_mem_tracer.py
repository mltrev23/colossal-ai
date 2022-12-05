from copy import deepcopy

import numpy as np
import torch

from colossalai.gemini.memory_tracer.model_data_memtracer import GLOBAL_CUDA_MEM_INFO
from colossalai.gemini.memory_tracer.runtime_mem_tracer import RuntimeMemTracer
from colossalai.utils.model.colo_init_context import ColoInitContext
from tests.components_to_test import run_fwd_bwd
from tests.components_to_test.registry import non_distributed_component_funcs


def test_runtime_mem_tracer():
    test_models = ['gpt2', 'bert', 'simple_net', 'repeated_computed_layers', 'nested_model', 'albert']

    for model_name in test_models:
        get_components_func = non_distributed_component_funcs.get_callable(model_name)
        model_builder, train_dataloader, _, _, criterion = get_components_func()

        with ColoInitContext(device='cpu'):
            model = model_builder(checkpoint=False)

        model_bk = deepcopy(model)
        runtime_mem_tracer = RuntimeMemTracer(model)

        for i, (data, label) in enumerate(train_dataloader):
            if i > 1:
                break
            data = data.cuda()
            label = label.cuda()

            run_fwd_bwd(runtime_mem_tracer, data, label, criterion, optimizer=runtime_mem_tracer)

        for p1, p2 in zip(model_bk.parameters(), model.parameters()):
            torch.allclose(p1.to(torch.half), p2)

        cuda_non_model_data_list = np.array(GLOBAL_CUDA_MEM_INFO.non_model_data_list) / 1024**2
        print("cuda_non_model_data_list", len(cuda_non_model_data_list))
        print(GLOBAL_CUDA_MEM_INFO.non_model_data_list)

        del model


if __name__ == '__main__':
    test_runtime_mem_tracer()
