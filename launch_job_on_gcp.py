import hydra

from hydra.utils import instantiate

from configs import register_config
from configs import Config
from instance_group_creator import InstanceGroupCreator
from utils import JobInfo


@hydra.main(config_path=".", config_name="config", version_base="1.3")
def run(config: Config) -> None:
    instance_group_creator: InstanceGroupCreator = instantiate(config.infrastructure.instance_group_creator)
    instance_ids = instance_group_creator.launch_instance_group()
    job_info = JobInfo(
        project_id=config.infrastructure.project_id,
        zone=config.infrastructure.zone,
        instance_group_name=config.infrastructure.instance_group_creator.name,
        instance_ids=instance_ids,
    )
    job_info.print_job_info()


if __name__ == "__main__":
    register_config()
    run()