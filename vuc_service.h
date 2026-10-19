#ifndef MQSIM_VUC_SERVICE_H
#define MQSIM_VUC_SERVICE_H

#include <stdint.h>
#include <stddef.h>
#include <string>
#include <vector>

#include "../nvmevirt/vuc_protocol.h"
#include "mqsim_native_online_model.h"

struct VUCServiceConfig {
	std::string neighbor_path;
	std::string vector_path;
	std::string node_to_nbr_slot_path;
	std::string node_to_vector_slot_path;
	std::string neighbor_page_lba_path;
	std::string vector_page_lba_path;
	uint32_t max_async_reads = 16;
	uint32_t idma_units = 1;
	uint32_t dbuf_capacity_pages = 32;
	uint32_t dlm_capacity_pages = 32;
	uint32_t parser_units = 1;
	/* Assumed baseline, not vendor measurements; see docs/vuc_timing_model.md. */
	uint64_t input_dma_ns_per_4k = 1024;
	uint64_t idma_ns_per_4k = 2048;
	uint64_t idma_setup_ns = 200;
	uint64_t ecc_ns_per_4k = 1000;
	uint64_t candidate_update_ns_per_node = 20;
	std::string trace_path;
	uint64_t neighbor_parse_ns_per_id = 10;
	uint64_t dsp_ns_per_float_component = 2;
	uint64_t dsp_setup_ns_per_page = 64;
	uint32_t dsp_units = 1;
	uint64_t output_dma_ns_per_4k = 1024;
};

class MQSimVUCService {
public:
	MQSimVUCService(MQSimNativeOnlineModel& model, const VUCServiceConfig& config);
	/* shared_buffer models a registered DMA aperture; all *_ref fields are byte
	 * offsets into it. Host must retain it until this method returns. */
	nvmev_vuc_completion execute(const nvmev_vuc_expand_filter_request& request,
				     void *shared_buffer, size_t shared_buffer_bytes,
				     uint64_t c3_start_time_ns, uint64_t *c3_finish_time_ns);

private:
	MQSimNativeOnlineModel& model_;
	VUCServiceConfig config_;
	std::vector<uint8_t> neighbors_;
	std::vector<uint8_t> vectors_;
	std::vector<uint32_t> node_to_nbr_slot_;
	std::vector<uint32_t> node_to_vector_slot_;
	std::vector<uint64_t> neighbor_page_lba_;
	std::vector<uint64_t> vector_page_lba_;
	uint32_t node_count_;
	uint32_t dimension_;
	uint32_t max_degree_;
	uint32_t nodes_per_nbr_page_;
	uint32_t vectors_per_page_;
	uint64_t next_internal_request_id_;

	bool range_valid(uint64_t offset, uint64_t bytes, size_t aperture_bytes) const;
};

#endif
