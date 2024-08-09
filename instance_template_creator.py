from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from google.cloud import compute_v1

from utils import get_logger, wait_for_extended_operation


# Virtual machine type enum class
class VMType(Enum):
    STANDARD = "STANDARD"
    SPOT = "SPOT"
    PREEMPTIBLE = "PREEMPTIBLE"
    

# Boot disk dataclass
@dataclass
class BootDiskConfig:
    project_id: str
    image_name: str
    size_gb: int
    labels: dict[str, str]
    

# Virtual Machine configuration dataclass    
@dataclass
class VMConfig:
    machine_type: str
    accelerator_count: int
    accelerator_type: str 
    vm_type: VMType
    disks: list[str]
    

# Virtual machine metadata dataclass    
@dataclass
class VMMetadataConfig:
    zone: str
    instance_group_name: str
    node_count: int
    disks: list[str]
    docker_image: str
    mlflow_tracking_uri: str
    python_hash_seed: int
    

# VM instance template class
class InstanceTemplateCreator:
    '''
    [Key component]
        1) Boot disk
        2) Network
        3) VM instance configuration
        4) Metadata used when VM instance is created
        5) (Optional) SSD disk can be attached to VM instance
    '''
    def __init__(
        self,
        scopes: list[str],   # access control
        network: str,
        subnetwork: str,
        vm_config: VMConfig,
        boot_disk_config: BootDiskConfig,
        vm_metadata_config: VMMetadataConfig,
        startup_script_path: str,
        template_name: str,
        project_id: str,
        labels: dict[str, str] = {}
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        
        self.scopes = scopes
        self.network = network
        self.subnetwork = subnetwork
        self.startup_script_path = startup_script_path
        self.vm_config = vm_config
        self.boot_disk_config = boot_disk_config
        self.vm_metadata_config = vm_metadata_config
        self.template_name = template_name.lower()
        self.project_id = project_id
        self.labels = labels
        
        self.template = compute_v1.InstanceTemplate()
        self.template.name = self.template_name
        
    # Core method for instance template class
    def create_template(self) -> compute_v1.InstanceTemplate:
        self.logger.info("Started creating instance template...")
        self.logger.info(f"{self.vm_metadata_config=}")

        self._create_book_disk()
        self._attach_disks()
        self._create_network_interface()
        self._create_machine_configuration()
        self._attach_metadata()

        self.logger.info("Creating instance template...")
        template_client = compute_v1.InstanceTemplatesClient()
        operation = template_client.insert(project=self.project_id, instance_template_resource=self.template)
        wait_for_extended_operation(operation, "instance template creation")

        self.logger.info("Instance template has been created...")
        return template_client.get(project=self.project_id, instance_template=self.template_name)

    def _create_book_disk(self) -> None:
        # Make disk instance and disk initialization instance
        boot_disk = compute_v1.AttachedDisk()
        boot_disk_initialize_params = compute_v1.AttachedDiskInitializeParams()
        
        # load image
        boot_disk_image = self._get_disk_image(self.boot_disk_config.project_id, self.boot_disk_config.image_name)
        
        # Define parameters for boot disk 
        boot_disk_initialize_params.source_image = boot_disk_image.self_link
        boot_disk_initialize_params.disk_size_gb = self.boot_disk_config.size_gb
        boot_disk_initialize_params.labels = self.boot_disk_config.labels
        boot_disk.initialize_params = boot_disk_initialize_params
        boot_disk.auto_delete = True   # auto-delete disk after finishing booting vm instance
        boot_disk.boot = True
        boot_disk.device_name = self.boot_disk_config.image_name

        self.template.properties.disks = [boot_disk]

    def _get_disk_image(self, project_id: str, image_name: str) -> compute_v1.Image:
        image_client = compute_v1.ImagesClient()
        return image_client.get(project=project_id, image=image_name)

    def _attach_disks(self) -> None:
        disk_names = self.vm_config.disks
        
        for disk_name in disk_names:
            disk = compute_v1.AttachedDisk(
                auto_delete=False, 
                boot=False, 
                mode="READ_ONLY",   # Only use READ_ONLY mode when attach SSD into VM instance
                device_name=disk_name, 
                source=disk_name
            )
            self.template.properties.disks.append(disk)

        if len(disk_names) > 0:
            self.template.properties.metadata.items.append(compute_v1.Items(key="disks", value="\n".join(disk_names)))

    def _create_network_interface(self) -> None:
        network_interface = compute_v1.NetworkInterface()
        
        network_interface.name = "nic0"   # default network in GCP
        network_interface.network = self.network
        network_interface.subnetwork = self.subnetwork
        
        # Add access config to assign an external IP
        access_config = compute_v1.AccessConfig(
            network_tier="PRIMIUM",
            type_="ONE_TO_ONE_NAT"
        )

        network_interface.access_configs = [access_config]
        self.template.properties.network_interfaces = [network_interface]

    def _create_machine_configuration(self) -> None:
        # Machine type
        self.template.properties.machine_type = self.vm_config.machine_type
        
        # (Optional) Accelerator (GPU, TPU etc..)
        if self.vm_config.accelerator_count > 0:
            self.template.properties.guest_accelerators = [
                compute_v1.AcceleratorConfig(
                    accelerator_type=self.vm_config.accelerator_type,
                    accelerator_count=self.vm_config.accelerator_count,
                )
            ]
        
        # Service account & labels
        self.template.properties.service_accounts = [compute_v1.ServiceAccount(email="default", scopes=self.scopes)]
        self.template.properties.labels = self.labels

        # Define VM instance scheduling: Preemptible vs Spot vs Standard
        vm_type = self.vm_config.vm_type
        if vm_type == VMType.PREEMPTIBLE:
            self.logger.info("Using PREEMPTIBLE machine")
            self.template.properties.scheduling = compute_v1.Scheduling(preemptible=True)
        elif vm_type == VMType.SPOT:
            self.logger.info("Using SPOT machine")
            self.template.properties.scheduling = compute_v1.Scheduling(
                provisioning_model=compute_v1.Scheduling.ProvisioningModel.SPOT.name,
                on_host_maintenance=compute_v1.Scheduling.OnHostMaintenance.TERMINATE.name,
            )
        elif vm_type == VMType.STANDARD:
            self.logger.info("Using STANDARD machine")
            self.template.properties.scheduling = compute_v1.Scheduling(
                provisioning_model=compute_v1.Scheduling.ProvisioningModel.STANDARD.name,
                on_host_maintenance=compute_v1.Scheduling.OnHostMaintenance.TERMINATE.name,
            )
        else:
            raise RuntimeError(f"Unsupported {vm_type=}")

    def _attach_metadata(self) -> None:
        # Define startup script that will be used after booting VM instance 
        startup_script = self._read_startup_script(self.startup_script_path)
        self.template.properties.metadata.items.append(compute_v1.Items(key="startup-script", value=startup_script))

        # Update metadata in template
        for meta_data_name, meta_data_value in self.vm_metadata_config.items():  # type: ignore
            self.template.properties.metadata.items.append(
                compute_v1.Items(key=meta_data_name, value=str(meta_data_value))
            )

    def _read_startup_script(self, startup_script_path: str) -> str:
        return Path(startup_script_path).read_text()