#ifndef MQSIM_ONLINE_MODEL_H
#define MQSIM_ONLINE_MODEL_H

#include <stdint.h>
#include <stdio.h>
#include <string>
#include <unordered_map>
#include <vector>

#include "../nvmevirt/mqsim_ipc_protocol.h"

class MQSimOnlineModel {
public:
	MQSimOnlineModel();

	bool load_config(const std::string& path, std::string *error);
	uint64_t preload_write(uint64_t slba, uint32_t nlb);
	uint64_t estimate_latency_ns(const nvmev_mqsim_io_msg& req);
	std::string describe() const;
	const std::string& last_trace() const;
	void print_stats(FILE *out) const;
	void print_compact_stats(FILE *out, size_t top_n) const;

private:
	struct Config {
		uint32_t channel_count;
		uint32_t channel_width;
		uint32_t channel_transfer_rate_mtps;
		uint32_t chips_per_channel;
		uint32_t dies_per_chip;
		uint32_t planes_per_die;
		uint32_t page_capacity_bytes;
		uint64_t read_latency_ns;
		uint64_t write_latency_ns;
		uint64_t controller_overhead_ns;
	};

	struct PPA {
		uint32_t channel;
		uint32_t chip;
		uint32_t die;
		uint32_t plane;
		uint32_t lun;
	};

	Config cfg;
	std::unordered_map<uint64_t, PPA> mapping_table;
	std::vector<uint64_t> lun_next_available_ns;
	std::vector<uint64_t> channel_next_available_ns;
	std::vector<uint64_t> lun_read_ops;
	std::vector<uint64_t> lun_write_ops;
	std::vector<uint64_t> channel_read_ops;
	std::vector<uint64_t> channel_write_ops;
	uint64_t total_requests;
	uint64_t total_pages;
	uint64_t preload_pages;
	uint64_t write_allocated_pages;
	uint64_t read_mapped_pages;
	uint64_t read_unmapped_pages;
	uint64_t next_physical_page;
	std::string last_trace_line;

	uint64_t transfer_latency_ns(uint64_t bytes) const;
	uint64_t page_service_latency_ns(uint32_t opcode, uint64_t bytes) const;
	uint64_t map_lpn_to_channel(uint64_t lpn) const;
	uint64_t map_lpn_to_chip(uint64_t lpn) const;
	uint64_t map_lpn_to_die(uint64_t lpn) const;
	uint64_t map_lpn_to_plane(uint64_t lpn) const;
	uint64_t map_lpn_to_lun(uint64_t lpn) const;
	PPA deterministic_ppa(uint64_t lpn) const;
	PPA allocate_physical_page();
	PPA resolve_read_ppa(uint64_t lpn, bool *mapped) const;
	void reset_resources();
};

#endif
