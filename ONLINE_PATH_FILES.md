# MQSim Files Used by the NVMeVirt Native-Online Path

This note documents the MQSim files that matter for the current NVMeVirt + MQSim native-online integration. In this path, MQSim is used as the backend SSD timing model: NVMeVirt sends logical request metadata to `MQSimIPCDaemon`, the daemon injects requests into MQSim, MQSim computes FTL/transaction/NAND timing, and the daemon returns the simulated backend latency.

## Build And Configuration

| File | Role |
|---|---|
| `Makefile` | Builds the standalone `MQSim` binary and the `MQSimIPCDaemon` binary. After changing MQSim headers, use a clean rebuild because the Makefile does not fully track header dependencies. |
| `ssdconfig.xml` | Active SSD configuration used by the daemon in experiments. Important parameters include `IO_Queue_Depth`, `CMT_Capacity`, channel count, chips per channel, dies per chip, plane count, page size, read latency, and channel transfer rate. |
| `workload.xml` | Standard MQSim standalone workload configuration. The native-online path does not primarily use this, because requests come from NVMeVirt through IPC. |
| `README.md` | Upstream MQSim documentation. Keep it as general MQSim reference; this file is not specific to the NVMeVirt online integration. |

## IPC Daemon And Online Injection

| File | Role |
|---|---|
| `mqsim_ipc_daemon.cpp` | Userspace bridge between NVMeVirt and MQSim. It opens `/dev/nvmev-mqsim-ipc`, mmaps shared rings, pops requests, calls the native-online model, pushes replies, and prints daemon bridge timing statistics. |
| `mqsim_native_online_model.h` | Declares the wrapper class that owns a live MQSim SSD model and exposes request submission/completion APIs to the daemon. It also stores pending requests and counters used by compact stats. |
| `mqsim_native_online_model.cpp` | Builds MQSim for online/native mode, injects host requests using `Host_Interface_NVMe::Submit_online_request()`, advances the MQSim event engine, handles completions, tracks flash transaction distribution, and prints queue/timing stats. |
| `../nvmevirt/mqsim_ipc_protocol.h` | Shared IPC protocol included by the daemon. This must stay ABI-compatible with the kernel module because request/reply structs are shared through mmap. |

## MQSim Event Engine

| File | Role |
|---|---|
| `src/sim/Engine.h` / `src/sim/Engine.cpp` | Central discrete-event simulation engine. The native-online path uses `Run_next_event()`, `Fast_forward()`, `Has_pending_event()`, and `Next_event_time()` to advance simulated time without going backward. |
| `src/sim/EventTree.h` / `src/sim/EventTree.cpp` | Priority structure for simulator events. It keeps the next event ordered by simulation timestamp. |
| `src/sim/Sim_Event.h` | Simulator event object definition. PHY timing, chip execution, and host/device events are represented through these events. |
| `src/sim/Sim_Object.h` | Base class for components that receive simulator events. Host interface, FTL, TSU, PHY, and flash chips are simulator objects or interact with simulator objects. |
| `src/sim/Sim_Defs.h` | Shared simulation types such as `sim_time_type` and `INVALID_TIME`. Many timing fields and instrumentation timestamps use these definitions. |

## Model Construction

| File | Role |
|---|---|
| `src/exec/Execution_Parameter_Set.h/.cpp` | Parses and stores the execution/configuration tree. The online model creates an execution parameter set from `ssdconfig.xml`. |
| `src/exec/Host_Parameter_Set.h/.cpp` | Holds host interface configuration such as PCIe and response-time logging options. In native-online mode, not all full host-interface timing is used because requests are injected directly. |
| `src/exec/Device_Parameter_Set.h/.cpp` | Holds SSD device configuration including NVMe queue depth, cache settings, mapping policy, TSU policy, channel count, and flash organization. |
| `src/exec/Flash_Parameter_Set.h/.cpp` | Holds NAND parameters such as read/program/erase latencies, dies per chip, planes per die, pages per block, and page capacity. |
| `src/exec/IO_Flow_Parameter_Set.h/.cpp` | Defines workload/flow settings. The native-online wrapper creates a single synthetic online flow so NVMeVirt requests can enter MQSim. |
| `src/exec/SSD_Device.h/.cpp` | Builds and connects the SSD model components: host interface, firmware, FTL, TSU, PHY, channels, chips, and flash hierarchy. |
| `src/exec/Host_System.h/.cpp` | Standard MQSim host-system wrapper. It is more important for standalone MQSim than for the current direct native-online injection path. |

## Host Interface Layer

| File | Role |
|---|---|
| `src/ssd/Host_Interface_NVMe.h/.cpp` | MQSim NVMe host interface model. In the current online path, `Submit_online_request()` is called directly, so the request bypasses the full SQ fetch/doorbell/CQE host queue behavior but still enters MQSim as a `User_Request`. |
| `src/ssd/Host_Interface_Base.h/.cpp` | Base host interface functionality and request-arrival/completion signal hooks. The online model listens for completed user requests through these callbacks. |
| `src/ssd/User_Request.h/.cpp` | Represents a host-level user request inside MQSim. A single NVMeVirt read can become one MQSim user request, which may later be decomposed into one or more flash transactions. |
| `src/ssd/Host_Interface_Defs.h` and `src/ssd/Host_Interface_NVMe_Priorities.h` | Shared host interface definitions and priority classes used by NVMe requests and TSU queues. |

## FTL, Mapping, Cache, And Firmware

| File | Role |
|---|---|
| `src/ssd/NVM_Firmware.h/.cpp` | Coordinates major SSD firmware components. It connects the host interface, FTL, TSU, and PHY layers. |
| `src/ssd/FTL.h/.cpp` | Flash Translation Layer wrapper. It receives user requests and invokes address mapping, transaction scheduling, cache, GC, and wear-leveling components. |
| `src/ssd/Address_Mapping_Unit_Base.h/.cpp` | Base class for address mapping logic. It defines common interfaces for translating logical addresses into physical flash addresses. |
| `src/ssd/Address_Mapping_Unit_Page_Level.h/.cpp` | Page-level mapping implementation used by the current config. It manages CMT behavior and can generate mapping read transactions when address translation misses. |
| `src/ssd/Address_Mapping_Unit_Hybrid.h/.cpp` | Alternative hybrid mapping implementation. Not the active config if `Address_Mapping` is `PAGE_LEVEL`, but still part of MQSim. |
| `src/ssd/Data_Cache_Manager_Base.h/.cpp` | Base interface for SSD-side data cache management. |
| `src/ssd/Data_Cache_Manager_Flash_Advanced.h/.cpp` | Advanced cache manager used when `Caching_Mechanism` is `ADVANCED`. This can affect whether reads hit/miss cache and how transactions are generated. |
| `src/ssd/Data_Cache_Manager_Flash_Simple.h/.cpp` | Alternative simple cache manager. Not the main active path with the current advanced-cache config. |
| `src/ssd/Data_Cache_Flash.h/.cpp` | Flash data cache structure used by cache managers. |

## Transaction Scheduling Queues

| File | Role |
|---|---|
| `src/ssd/NVM_Transaction.h` | Base transaction structure. It stores transaction type/source and instrumentation timestamps such as TSU enqueue/dequeue, PHY issue, command transfer, die execution, data transfer, and completion time. |
| `src/ssd/NVM_Transaction_Flash.h/.cpp` | Base flash transaction type. User reads, mapping reads, writes, GC operations, and erases are represented as flash transactions. |
| `src/ssd/NVM_Transaction_Flash_RD.h/.cpp` | Flash read transaction implementation. Important for DiskANN/fio read experiments because host reads and mapping reads become read transactions. |
| `src/ssd/NVM_Transaction_Flash_WR.h/.cpp` | Flash write transaction implementation. Mostly relevant for preloading, writes, GC movement, or future write-heavy tests. |
| `src/ssd/NVM_Transaction_Flash_ER.h/.cpp` | Flash erase transaction implementation. Mostly relevant when GC or wear-leveling becomes active. |
| `src/ssd/Flash_Transaction_Queue.h/.cpp` | Queue container used by TSU and PHY waiting queues. It now records enqueue/dequeue stats and sets `STAT_tsu_enqueue_time` for transaction timing breakdown. |
| `src/ssd/Queue_Probe.h/.cpp` | Collects queue length, wait time, max wait, and related queue statistics. The printed queue congestion tables come from this instrumentation. |
| `src/ssd/TSU_Base.h/.cpp` | Base transaction scheduling unit. It selects ready transactions, records TSU dequeue time, and dispatches them to the NVM controller/PHY. |
| `src/ssd/TSU_Priority_OutOfOrder.h/.cpp` | Current active TSU policy when `Transaction_Scheduling_Policy` is `PRIORITY_OUT_OF_ORDER`. It owns per-channel/per-chip user, mapping, and GC queues and prints the internal queue congestion table. |
| `src/ssd/TSU_OutofOrder.h/.cpp` | Alternative out-of-order scheduler without the same priority structure. Useful for comparison if the scheduling policy changes. |
| `src/ssd/TSU_FLIN.h/.cpp` | Alternative scheduler. Not the current active policy but still part of MQSim. |

## NAND / PHY / Channel Timing

| File | Role |
|---|---|
| `src/ssd/NVM_PHY_Base.h/.cpp` | Base PHY interface. The TSU dispatches flash transactions through this abstraction. |
| `src/ssd/NVM_PHY_ONFI.h/.cpp` | Base ONFI PHY layer with common signal callbacks. |
| `src/ssd/NVM_PHY_ONFI_NVDDR2.h/.cpp` | Main NAND timing path for the current `NVDDR2` config. It sends commands to chips, models command/address transfer, die execution, data-out transfer, PHY waiting queues, and prints read timing breakdown. |
| `src/ssd/ONFI_Channel_Base.h/.cpp` | Base channel abstraction used by ONFI channels. |
| `src/ssd/ONFI_Channel_NVDDR2.h/.cpp` | Calculates ONFI NVDDR2 command/data transfer timing from low-level timing parameters. Read command time and data-out setup/transfer behavior originate here. |
| `src/ssd/NVM_Channel_Base.h` | Common channel-level definitions used by NAND timing components. |

## Flash Chip Hierarchy

| File | Role |
|---|---|
| `src/nvm_chip/NVM_Chip.h` | Base class for flash chip objects. |
| `src/nvm_chip/NVM_Memory_Address.h` and `src/nvm_chip/NVM_Types.h` | Shared NAND address and type definitions. |
| `src/nvm_chip/flash_memory/Flash_Chip.h/.cpp` | Models flash chip and die execution. `Get_command_execution_latency()` returns NAND read/program/erase execution latency based on page type and flash technology. |
| `src/nvm_chip/flash_memory/Flash_Command.h` | Flash command representation used when PHY issues commands to chips. |
| `src/nvm_chip/flash_memory/Die.h/.cpp` | Die-level state. A die can be busy while a command execution event is pending. |
| `src/nvm_chip/flash_memory/Plane.h/.cpp`, `Block.h/.cpp`, `Page.h` | Physical NAND hierarchy used for page/block state, metadata, and placement. |
| `src/nvm_chip/flash_memory/Physical_Page_Address.h/.cpp` | Physical address representation for channel/chip/die/plane/block/page. TSU queue names and hot-LUN statistics ultimately derive from these address fields. |
| `src/nvm_chip/flash_memory/FlashTypes.h` | Flash technology and command-related enums. |

## GC, Wear-Leveling, And Statistics

| File | Role |
|---|---|
| `src/ssd/Flash_Block_Manager_Base.h/.cpp` and `src/ssd/Flash_Block_Manager.h/.cpp` | Track free/valid/invalid pages and block allocation state. These matter more when writes, GC, or preconditioning are enabled. |
| `src/ssd/GC_and_WL_Unit_Base.h/.cpp` and `src/ssd/GC_and_WL_Unit_Page_Level.h/.cpp` | Garbage collection and wear-leveling logic. In read-only experiments they should not dominate, but their queues are still present in TSU stats. |
| `src/ssd/Stats.h/.cpp` | Global MQSim stats counters. Some issued-command counters and backend operation counts are maintained here. |

## Utilities

| File | Role |
|---|---|
| `src/utils/XMLWriter.h/.cpp` and `src/utils/rapidxml/` | XML parsing/writing support for config and result files. |
| `src/utils/StringTools.h/.cpp` | String parsing helpers used while loading configurations. |
| `src/utils/Logical_Address_Partitioning_Unit.h/.cpp` | Maps streams to channel/chip/die/plane sets. This affects how host requests are distributed across backend resources. |
| `src/utils/RandomGenerator.h/.cpp` and `CMRRandomGenerator.h/.cpp` | Random number utilities used by MQSim components and workload generation. The native-online path still uses MQSim internals that may depend on these helpers. |
| `src/utils/Helper_Functions.h/.cpp` | General helper functions used across MQSim. |

## Generated Binaries

| File | Role |
|---|---|
| `MQSim` | Standalone MQSim executable. Useful for original MQSim workloads, but not the primary binary for NVMeVirt online experiments. |
| `MQSimIPCDaemon` | Userspace daemon used by the current integration. It must be rebuilt together with the kernel module when the IPC protocol changes. |
| `build/` | Object files generated by the MQSim build. Usually local build output rather than source to document or commit. |

## Current Timing Interpretation

In the native-online integration, MQSim primarily models backend SSD timing: FTL mapping, CMT/mapping reads, TSU queues, channel/chip/die timing, and NAND command/data transfer. The full original MQSim NVMe SQ/CQ fetch path is not exercised because `MQSimNativeOnlineModel` directly calls `Host_Interface_NVMe::Submit_online_request()`.

Useful daemon-side tables include:

```text
MQSim internal queue congestion table
MQSim read timing breakdown table
MQSim daemon bridge timing table
```

The queue congestion table corresponds mainly to TSU transaction queues. The read timing breakdown separates submit-to-TSU timing, TSU wait, command transfer, die execution, data transfer, and dispatch-to-done timing.
