import torch
import torch.nn as nn
import torch.nn.functional as F
from colossalai.nn import CheckpointModule
from .utils.dummy_data_generator import DummyDataGenerator
from .registry import non_distributed_component_funcs


class SimpleNet(CheckpointModule):
    """
    In this no-leaf module, it has subordinate nn.modules and a nn.Parameter.
    """

    def __init__(self, checkpoint=False) -> None:
        super().__init__(checkpoint=checkpoint)
        self.proj1 = nn.Linear(4, 8)
        self.proj2 = nn.Linear(8, 4)

    def forward(self, x):
        x = self.proj1(x)
        x = self.proj2(x)
        return x


class DummyDataLoader(DummyDataGenerator):

    def generate(self):
        data = torch.rand(16, 4)
        label = torch.randint(low=0, high=2, size=(16,))
        return data, label


@non_distributed_component_funcs.register(name='simple_net')
def get_training_components():

    def model_builder(checkpoint=True):
        return SimpleNet(checkpoint)

    trainloader = DummyDataLoader()
    testloader = DummyDataLoader()

    criterion = torch.nn.CrossEntropyLoss()
    from colossalai.nn.optimizer import HybridAdam
    return model_builder, trainloader, testloader, HybridAdam, criterion
