#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>
#include "IO_Flow_Closed_Loop_Replay.h"

namespace Host_Components
{
IO_Flow_Closed_Loop_Replay::IO_Flow_Closed_Loop_Replay(const sim_object_id_type &name, uint16_t flow_id, LHA_type start_lsa_on_device, LHA_type end_lsa_on_device, uint16_t io_queue_id,
													   uint16_t nvme_submission_queue_size, uint16_t nvme_completion_queue_size, IO_Flow_Priority_Class::Priority priority_class, double initial_occupancy_ratio,
													   std::string trace_file_path, std::string schedule_file_path,
													   HostInterface_Types SSD_device_type, PCIe_Root_Complex *pcie_root_complex, SATA_HBA *sata_hba,
													   bool enabled_logging, sim_time_type logging_period, std::string logging_file_path)
	: IO_Flow_Trace_Based(name, flow_id, start_lsa_on_device, end_lsa_on_device, io_queue_id, nvme_submission_queue_size, nvme_completion_queue_size, priority_class,
						  initial_occupancy_ratio, trace_file_path, Trace_Time_Unit::NANOSECOND, 1, 100, SSD_device_type, pcie_root_complex, sata_hba,
						  enabled_logging, logging_period, logging_file_path),
	  schedule_file_path(schedule_file_path)
{
}

IO_Flow_Closed_Loop_Replay::~IO_Flow_Closed_Loop_Replay()
{
	if (round_log != NULL)
		std::fclose(round_log);
}

Host_IO_Request *IO_Flow_Closed_Loop_Replay::Generate_next_request()
{
	return NULL; //Requests are created by Execute_simulator_event when their round issues them
}

void IO_Flow_Closed_Loop_Replay::Start_simulation()
{
	IO_Flow_Base::Start_simulation(); //not the trace-based start: the ASCII trace is not replayed by timestamp
	std::ifstream in(schedule_file_path);
	if (!in.is_open())
		PRINT_ERROR("Cannot open closed-loop replay schedule: " << schedule_file_path)
	std::map<uint32_t, uint32_t> worker_index;
	std::string line;
	size_t line_no = 0;
	const LHA_type span = end_lsa_on_device - start_lsa_on_device;
	while (std::getline(in, line))
	{
		line_no++;
		if (line.empty() || line[0] == '#')
			continue;
		std::istringstream ss(line);
		uint32_t worker;
		Replay_Round round;
		if (!(ss >> worker >> round.query >> round.gap >> round.post >> round.n))
			PRINT_ERROR("Bad replay schedule line " << line_no)
		round.first_read = reads.size();
		for (unsigned int i = 0; i < round.n; i++)
		{
			Replay_Read r;
			if (!(ss >> r.offset >> r.lba >> r.sectors) || r.sectors == 0 || r.lba + r.sectors > span)
				PRINT_ERROR("Bad read " << i << " on replay schedule line " << line_no)
			reads.push_back(r);
		}
		auto it = worker_index.find(worker);
		if (it == worker_index.end())
		{
			it = worker_index.emplace(worker, (uint32_t)workers.size()).first;
			workers.emplace_back();
		}
		workers[it->second].rounds.push_back(round);
	}
	if (workers.empty())
		PRINT_ERROR("Empty closed-loop replay schedule: " << schedule_file_path)
	total_requests_to_be_generated = (unsigned int)reads.size();
	const char *path = std::getenv("MQSIM_ROUND_LOG");
	if (path != NULL && path[0] != '\0')
	{
		round_log = std::fopen(path, "w");
		if (round_log == NULL)
			PRINT_ERROR("Cannot open MQSIM_ROUND_LOG " << path)
		std::fprintf(round_log, "worker query round start_ns last_complete_ns end_ns n\n");
	}
	PRINT_MESSAGE("Closed-loop replay: " << workers.size() << " workers, " << reads.size() << " reads from " << schedule_file_path);
	for (uint32_t w = 0; w < workers.size(); w++)
		start_round(w, workers[w].rounds[0].gap);
}

void IO_Flow_Closed_Loop_Replay::start_round(uint32_t worker, sim_time_type start_time)
{
	Worker_State &ws = workers[worker];
	const Replay_Round &round = ws.rounds[ws.current];
	ws.round_start = start_time;
	ws.issued = 0;
	ws.outstanding = 0;
	ws.last_complete = start_time;
	if (round.n == 0)
	{
		Simulator->Register_sim_event(start_time, this, (void *)(intptr_t)worker, EMPTY_ROUND_DONE);
		return;
	}
	for (unsigned int i = 0; i < round.n; i++)
	{
		intptr_t key = ((intptr_t)worker << 32) | (intptr_t)i;
		Simulator->Register_sim_event(start_time + reads[round.first_read + i].offset, this, (void *)key, ISSUE_READ);
	}
}

void IO_Flow_Closed_Loop_Replay::finish_round(uint32_t worker, sim_time_type now)
{
	Worker_State &ws = workers[worker];
	const Replay_Round &round = ws.rounds[ws.current];
	const sim_time_type end = now + round.post;
	if (round_log != NULL)
		std::fprintf(round_log, "%u %lld %zu %llu %llu %llu %u\n", worker, (long long)round.query, ws.current, (unsigned long long)ws.round_start,
					 (unsigned long long)now, (unsigned long long)end, round.n);
	ws.current++;
	if (ws.current < ws.rounds.size())
		start_round(worker, end + ws.rounds[ws.current].gap);
}

void IO_Flow_Closed_Loop_Replay::Execute_simulator_event(MQSimEngine::Sim_Event *ev)
{
	if (ev->Type == EMPTY_ROUND_DONE)
	{
		finish_round((uint32_t)(intptr_t)ev->Parameters, Simulator->Time());
		return;
	}
	const intptr_t key = (intptr_t)ev->Parameters;
	const uint32_t worker = (uint32_t)(key >> 32);
	const unsigned int index = (unsigned int)(key & 0xffffffff);
	Worker_State &ws = workers[worker];
	const Replay_Read &r = reads[ws.rounds[ws.current].first_read + index];
	Host_IO_Request *request = new Host_IO_Request;
	request->Type = Host_IO_Request_Type::READ;
	request->Start_LBA = start_lsa_on_device + r.lba;
	request->LBA_count = r.sectors;
	request->Arrival_time = Simulator->Time();
	STAT_generated_request_count++;
	STAT_generated_read_request_count++;
	request_worker[request] = worker;
	ws.issued++;
	ws.outstanding++;
	Submit_io_request(request);
}

void IO_Flow_Closed_Loop_Replay::NVMe_consume_io_request(Completion_Queue_Entry *cqe)
{
	auto rq = nvme_software_request_queue.find(cqe->Command_Identifier);
	if (rq == nvme_software_request_queue.end() || rq->second == NULL)
		PRINT_ERROR("Closed-loop replay: completion for an unknown command id")
	auto wit = request_worker.find(rq->second);
	if (wit == request_worker.end())
		PRINT_ERROR("Closed-loop replay: completed request has no worker")
	const uint32_t worker = wit->second;
	request_worker.erase(wit);
	IO_Flow_Base::NVMe_consume_io_request(cqe); //statistics, MQSIM_REQUEST_LOG, deletes the request
	IO_Flow_Base::NVMe_update_and_submit_completion_queue_tail();
	Worker_State &ws = workers[worker];
	ws.outstanding--;
	ws.last_complete = Simulator->Time();
	if (ws.outstanding == 0 && ws.issued == ws.rounds[ws.current].n)
		finish_round(worker, Simulator->Time());
}
} // namespace Host_Components
