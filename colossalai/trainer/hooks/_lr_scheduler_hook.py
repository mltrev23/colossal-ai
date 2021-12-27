from colossalai.registry import HOOKS
from torch import Tensor

from ._metric_hook import LearningRateMetric, MetricHook


@HOOKS.register_module
class LRSchedulerHook(MetricHook):
    """Build LR scheduler

    :param trainer: Trainer attached with current hook
    :type trainer: Trainer
    :param lr_scheduler_cfg: The config of LR scheduler
    :type lr_scheduler_cfg: dict
    :param by_epoch: If `True`, the LR will be scheduled every epoch. Else, the LR will be scheduled every batch. Defaults to `True`.
    :type by_epoch: bool
    :param priority: Priority in the printing, hooks with small priority will be printed in front
    :type priority: int, optional
    """
    def __init__(
        self,
        lr_scheduler,
        by_epoch: bool,
        store_lr_in_state: bool = True,
        priority: int = 1,
    ):
        super().__init__(priority=priority)
        self.by_epoch = by_epoch
        self.lr_scheduler = lr_scheduler
        self.store_lr_in_state = store_lr_in_state

    def after_hook_is_attached(self, trainer):
        trainer.states['metrics']['train']['LR'] = LearningRateMetric(epoch_only=self.by_epoch,
                                                                      initial_lr=self.lr_scheduler.get_last_lr()[0])

    def after_train_epoch(self, trainer):
        if self.by_epoch:
            self.lr_scheduler.step()
            trainer.states['metrics']['train']['LR'].update(self.lr_scheduler.get_last_lr()[0])

    def after_train_iter(self, trainer, output: Tensor, label: Tensor, loss: Tensor):
        if not self.by_epoch:
            self.lr_scheduler.step()
            trainer.states['metrics']['train']['LR'].update(self.lr_scheduler.get_last_lr()[0])
