#ifndef MQSIM_NATIVE_ONLINE_MODEL_H
#define MQSIM_NATIVE_ONLINE_MODEL_H

#include <stdint.h>
#include <stdio.h>
#include <string>
#include <unordered_map>
#include <vector>

#include "../nvmevirt/mqsim_ipc_protocol.h"
#include "src/sim/Sim_Defs.h"

class SSD_Device;
class Execution_Parameter_Set;
class IO_Flow_Parameter_Set;

namespace SSD_Components {
class User_Request;
class Host_Interface_NVMe;
class NVM_Transaction_Flash;
}

class MQSimNativeOnlineModel {
public:
	MQSimNativeOnlineModel();
	~MQSimNativeOnlineModel();

	bool initialize(const std::string& ssd_config_path, std::string *error);
	uint64_t preload_write(uint64_t slba, uint32_t nlb);
	uint64_t estimate_latency_ns(const nvmev_mqsim_io_msg& req);
	std::string describe() const;
	const std::string& last_trace() const;
	void print_stats(FILE *out) const;
	void print_compact_stats(FILE *out, size_t top_n) const;

private:
	struct PendingRequest {
		uint64_t external_id;
		uint64_t submit_time_ns;
		bool completed;
		uint64_t completion_time_ns;
	};

	Execution_Parameter_Set *exec_params;
	IO_Flow_Parameter_Set *online_flow;
	SSD_Device *ssd;
	SSD_Components::Host_Interface_NVMe *host_interface;
	unsigned int sectors_per_page;
	uint64_t total_requests;
	uint64_t preload_requests;
	uint64_t preload_sectors;
		uint64_t read_requests;
		uint64_t write_requests;
		uint64_t completed_requests;
		uint64_t flash_read_transactions;
		uint64_t flash_write_transactions;
		uint64_t flash_erase_transactions;
		uint32_t channel_count;
		uint32_t chips_per_channel;
		uint32_t dies_per_chip;
		std::string last_trace_line;
		std::vector<uint64_t> channel_read_ops;
		std::vector<uint64_t> channel_write_ops;
		std::vector<uint64_t> channel_erase_ops;
		std::vector<uint64_t> lun_read_ops;
		std::vector<uint64_t> lun_write_ops;
		std::vector<uint64_t> lun_erase_ops;
		std::unordered_map<SSD_Components::User_Request *, PendingRequest *> pending_by_request;
	std::vector<PendingRequest *> pending_storage;
	static MQSimNativeOnlineModel *active_instance;

	bool load_config(const std::string& path, std::string *error);
	void create_single_online_flow();
	void start_online_engine();
		uint64_t submit_and_drain(uint32_t opcode, uint64_t slba, uint32_t nlb,
					  uint64_t submit_time_ns, uint64_t external_id);
		void handle_completion(SSD_Components::User_Request *request, sim_time_type completion_time);
		static void completion_trampoline(SSD_Components::User_Request *request, sim_time_type completion_time);
		void handle_flash_transaction(SSD_Components::NVM_Transaction_Flash *transaction);
		static void flash_transaction_trampoline(SSD_Components::NVM_Transaction_Flash *transaction);
		uint64_t lun_index(uint32_t channel, uint32_t chip, uint32_t die) const;
		void clear_pending();
};

#endif
