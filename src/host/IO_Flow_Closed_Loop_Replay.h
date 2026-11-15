#ifndef IO_FLOW_CLOSED_LOOP_REPLAY_H
#define IO_FLOW_CLOSED_LOOP_REPLAY_H

#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>
#include "IO_Flow_Trace_Based.h"

namespace Host_Components
{
/* Completion-dependent (closed-loop) replay of an application trace such as DiskANN's beam search.
 * Each worker replays a sequence of "rounds". A round issues its reads at measured offsets from the round start,
 * the worker then waits until every read of the round has completed *in the simulated SSD*, adds the measured host
 * time after the last completion (post) and the measured host compute gap before the next round, and starts the
 * next round. Host-side times come from the real run; only device time is simulated, so a faster or slower SSD
 * model changes when later rounds are issued, as in the real closed-loop application.
 *
 * Schedule file (one round per line, rounds of a worker in order):
 *   worker query gap_ns post_ns n off_ns lba sectors [off_ns lba sectors ...]
 * gap_ns of a worker's first round is measured from simulation start; later gaps from the previous round's end
 * (last simulated completion + its post_ns). LBAs are relative to the flow's start LSA.
 * The ASCII trace passed as File_Path is only used for preconditioning statistics (the same request multiset).
 * MQSIM_ROUND_LOG=<path> writes: worker query round_index start_ns last_complete_ns end_ns n
 */
class IO_Flow_Closed_Loop_Replay : public IO_Flow_Trace_Based
{
public:
	IO_Flow_Closed_Loop_Replay(const sim_object_id_type &name, uint16_t flow_id, LHA_type start_lsa_on_device, LHA_type end_lsa_on_device, uint16_t io_queue_id,
							   uint16_t nvme_submission_queue_size, uint16_t nvme_completion_queue_size, IO_Flow_Priority_Class::Priority priority_class, double initial_occupancy_ratio,
							   std::string trace_file_path, std::string schedule_file_path,
							   HostInterface_Types SSD_device_type, PCIe_Root_Complex *pcie_root_complex, SATA_HBA *sata_hba,
							   bool enabled_logging, sim_time_type logging_period, std::string logging_file_path);
	~IO_Flow_Closed_Loop_Replay();
	Host_IO_Request *Generate_next_request();
	void NVMe_consume_io_request(Completion_Queue_Entry *);
	void Start_simulation();
	void Execute_simulator_event(MQSimEngine::Sim_Event *);

private:
	enum Event_Type { ISSUE_READ = 0, EMPTY_ROUND_DONE = 1 };
	struct Replay_Read { sim_time_type offset; LHA_type lba; unsigned int sectors; };
	struct Replay_Round { int64_t query; sim_time_type gap; sim_time_type post; size_t first_read; unsigned int n; };
	struct Worker_State
	{
		std::vector<Replay_Round> rounds;
		size_t current = 0;
		unsigned int issued = 0, outstanding = 0;
		sim_time_type round_start = 0, last_complete = 0;
	};
	std::string schedule_file_path;
	std::vector<Replay_Read> reads;
	std::vector<Worker_State> workers;
	std::unordered_map<Host_IO_Request *, uint32_t> request_worker;
	FILE *round_log = NULL;
	void start_round(uint32_t worker, sim_time_type start_time);
	void finish_round(uint32_t worker, sim_time_type now);
};
} // namespace Host_Components

#endif // !IO_FLOW_CLOSED_LOOP_REPLAY_H
