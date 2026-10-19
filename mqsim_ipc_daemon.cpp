// SPDX-License-Identifier: GPL-2.0-only

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <cstring>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <sched.h>
#include <algorithm>
#include <string>
#include <time.h>
#include <memory>
#include <map>
#include <vector>
#include <signal.h>

#include "../nvmevirt/mqsim_ipc_protocol.h"
#include "mqsim_native_online_model.h"
#include "vuc_service.h"
#include "src/sim/Engine.h"

static uint64_t read_latency_ns = 75000;
static uint64_t write_latency_ns = 750000;
static uint64_t controller_overhead_ns = 20000;
static uint64_t transfer_per_4k_ns = 2000;
static const char *ssd_config_path = "ssdconfig.xml";
static const char *preload_extents_path = NULL;
enum class TimingModel { FIXED, NATIVE_ONLINE };
static TimingModel timing_model = TimingModel::FIXED;
static uint64_t trace_first_requests = 0;
static uint64_t stats_every_reqs = 0;
static uint64_t stats_top_n = 8;
static uint64_t completion_window_ns = 0;
static uint64_t request_coalesce_us = 10;
static uint64_t request_coalesce_max = 32;
static bool event_stats = false;
static uint64_t event_trace_first_events = 0;
static bool stats_full = false;
static std::string vuc_trace_path;
static std::string vuc_neighbor_path;
static std::string vuc_vector_path;
static std::string vuc_node_map_path;
static std::string vuc_vector_map_path;
static std::string vuc_neighbor_lba_map_path;
static std::string vuc_vector_lba_map_path;
static uint64_t vuc_input_dma_ns_per_4k = VUCServiceConfig{}.input_dma_ns_per_4k;
static uint64_t vuc_idma_ns_per_4k = VUCServiceConfig{}.idma_ns_per_4k;
static uint64_t vuc_parse_ns_per_id = VUCServiceConfig{}.neighbor_parse_ns_per_id;
static uint64_t vuc_dsp_ns_per_component = VUCServiceConfig{}.dsp_ns_per_float_component;
static uint64_t vuc_output_dma_ns_per_4k = VUCServiceConfig{}.output_dma_ns_per_4k;
static uint64_t vuc_max_async_reads = 16;
static uint64_t vuc_idma_units = 1;
static uint64_t vuc_dbuf_capacity_pages = VUCServiceConfig{}.dbuf_capacity_pages;
static uint64_t vuc_parser_units = 1;
static uint64_t vuc_dsp_units = 1;
static uint64_t vuc_dlm_capacity_pages = VUCServiceConfig{}.dlm_capacity_pages;
static uint64_t vuc_idma_setup_ns = VUCServiceConfig{}.idma_setup_ns;
static uint64_t vuc_ecc_ns_per_4k = VUCServiceConfig{}.ecc_ns_per_4k;
static uint64_t vuc_candidate_update_ns_per_node = VUCServiceConfig{}.candidate_update_ns_per_node;
static uint64_t vuc_dsp_setup_ns_per_page = VUCServiceConfig{}.dsp_setup_ns_per_page;
static volatile sig_atomic_t snapshot_requested = 0;
static volatile sig_atomic_t stop_requested = 0;

static void handle_signal(int signal_number)
{
	if (signal_number == SIGUSR1)
		snapshot_requested = 1;
	else
		stop_requested = 1;
}

struct DaemonTimingStats {
	uint64_t injection_count = 0;
	uint64_t late_injection_count = 0;
	uint64_t injection_ahead_total_ns = 0;
	uint64_t injection_ahead_max_ns = 0;
	uint64_t pop_delay_count = 0;
	uint64_t pop_delay_total_ns = 0;
	uint64_t pop_delay_max_ns = 0;
	uint64_t submit_count = 0;
	uint64_t submit_total_ns = 0;
	uint64_t submit_max_ns = 0;
	uint64_t run_completion_count = 0;
	uint64_t run_completion_total_ns = 0;
	uint64_t run_completion_max_ns = 0;
	uint64_t run_pending_before_total = 0;
	uint64_t run_pending_before_max = 0;
	uint64_t run_pending_after_total = 0;
	uint64_t run_pending_after_max = 0;
	uint64_t run_replies_total = 0;
	uint64_t run_replies_max = 0;
	uint64_t run_sim_advanced_total_ns = 0;
	uint64_t run_sim_advanced_max_ns = 0;
	uint64_t coalesce_round_count = 0;
	uint64_t coalesce_wait_total_ns = 0;
	uint64_t coalesce_wait_max_ns = 0;
	uint64_t coalesce_requests_total = 0;
	uint64_t coalesce_requests_max = 0;
	uint64_t push_response_count = 0;
	uint64_t push_response_total_ns = 0;
	uint64_t push_response_max_ns = 0;
};

static DaemonTimingStats daemon_timing_stats;
static std::map<std::pair<uint64_t, uint64_t>, std::vector<uint8_t>> vuc_result_cache;

static uint64_t monotonic_now_ns()
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void update_max_u64(uint64_t& current, uint64_t value)
{
	if (value > current)
		current = value;
}

static uint64_t avg_or_zero(uint64_t total, uint64_t count)
{
	return count == 0 ? 0 : total / count;
}

static void print_daemon_timing_stats(FILE *out)
{
	fprintf(out, "Injection timing (cumulative, excludes preload): requests=%" PRIu64
		" late=%" PRIu64 " ahead_total_ns=%" PRIu64 " ahead_max_ns=%" PRIu64
		" ahead_mean_ns=%.2f late_pct=%.2f\n",
		daemon_timing_stats.injection_count, daemon_timing_stats.late_injection_count,
		daemon_timing_stats.injection_ahead_total_ns, daemon_timing_stats.injection_ahead_max_ns,
		daemon_timing_stats.injection_count ? (double)daemon_timing_stats.injection_ahead_total_ns / daemon_timing_stats.injection_count : 0.0,
		daemon_timing_stats.injection_count ? 100.0 * daemon_timing_stats.late_injection_count / daemon_timing_stats.injection_count : 0.0);
	fprintf(out, "MQSim daemon bridge timing table:\n");
	fprintf(out, "%28s %12s %14s %14s\n", "Stage", "Count", "Avg(ns)", "Max(ns)");
	fprintf(out, "====================================================================\n");
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"KernelSubmit2DaemonPop",
		daemon_timing_stats.pop_delay_count,
		avg_or_zero(daemon_timing_stats.pop_delay_total_ns, daemon_timing_stats.pop_delay_count),
		daemon_timing_stats.pop_delay_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"DaemonSubmitRequest",
		daemon_timing_stats.submit_count,
		avg_or_zero(daemon_timing_stats.submit_total_ns, daemon_timing_stats.submit_count),
		daemon_timing_stats.submit_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RunUntilCompletion",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_completion_total_ns, daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_completion_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RequestCoalesceWait",
		daemon_timing_stats.coalesce_round_count,
		avg_or_zero(daemon_timing_stats.coalesce_wait_total_ns,
			    daemon_timing_stats.coalesce_round_count),
		daemon_timing_stats.coalesce_wait_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RequestsPerCoalesce",
		daemon_timing_stats.coalesce_round_count,
		avg_or_zero(daemon_timing_stats.coalesce_requests_total,
			    daemon_timing_stats.coalesce_round_count),
		daemon_timing_stats.coalesce_requests_max);
	fprintf(out, "\nMQSim completion batch table:\n");
	fprintf(out, "%28s %12s %14s %14s\n", "Metric", "Count", "Avg", "Max");
	fprintf(out, "====================================================================\n");
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PendingBeforeRun",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_pending_before_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_pending_before_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RepliesGenerated",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_replies_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_replies_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PendingAfterRun",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_pending_after_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_pending_after_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"SimAdvanced(ns)",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_sim_advanced_total_ns,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_sim_advanced_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PushResponse",
		daemon_timing_stats.push_response_count,
		avg_or_zero(daemon_timing_stats.push_response_total_ns, daemon_timing_stats.push_response_count),
		daemon_timing_stats.push_response_max_ns);
}

static uint32_t load_u32(const uint32_t *ptr)
{
	return __atomic_load_n(ptr, __ATOMIC_ACQUIRE);
}

static void store_u32(uint32_t *ptr, uint32_t value)
{
	__atomic_store_n(ptr, value, __ATOMIC_RELEASE);
}

static bool ring_empty(const nvmev_mqsim_ring_header& ring)
{
	return load_u32(&ring.head) == load_u32(&ring.tail);
}

static bool ring_full(const nvmev_mqsim_ring_header& ring)
{
	return load_u32(&ring.tail) - load_u32(&ring.head) >= load_u32(&ring.size);
}

static uint64_t estimate_latency_ns(const nvmev_mqsim_io_msg& req)
{
	uint64_t bytes = (uint64_t)req.nlb * 512;
	uint64_t chunks_4k = (bytes + 4095) / 4096;
	uint64_t transfer_ns = chunks_4k * transfer_per_4k_ns;

	if (req.opcode == NVMEV_MQSIM_IO_WRITE)
		return controller_overhead_ns + transfer_ns + write_latency_ns;

	return controller_overhead_ns + transfer_ns + read_latency_ns;
}

static bool pop_request(nvmev_mqsim_shm *shm, nvmev_mqsim_io_msg *req)
{
	static bool prefer_vuc = true;
	nvmev_mqsim_ring_header *ring;
	nvmev_mqsim_io_msg *entries;
	uint32_t mask;
	uint32_t head;

	if (prefer_vuc && !ring_empty(shm->vuc_req_ring)) {
		ring = &shm->vuc_req_ring;
		entries = shm->vuc_req_entries;
		mask = NVMEV_MQSIM_VUC_RING_MASK;
	} else if (!ring_empty(shm->req_ring)) {
		ring = &shm->req_ring;
		entries = shm->req_entries;
		mask = NVMEV_MQSIM_RING_MASK;
		prefer_vuc = true;
	} else if (!ring_empty(shm->vuc_req_ring)) {
		ring = &shm->vuc_req_ring;
		entries = shm->vuc_req_entries;
		mask = NVMEV_MQSIM_VUC_RING_MASK;
	} else {
		return false;
	}

	head = load_u32(&ring->head);
	__atomic_thread_fence(__ATOMIC_ACQUIRE);
	memcpy(req, &entries[head & mask], sizeof(*req));
	store_u32(&ring->head, head + 1);
	prefer_vuc = req->type != NVMEV_MQSIM_MSG_VUC_REQUEST;
	return true;
}

static bool push_response(nvmev_mqsim_shm *shm, const nvmev_mqsim_io_msg& resp)
{
	nvmev_mqsim_ring_header *ring;
	nvmev_mqsim_io_msg *entries;
	uint32_t mask;
	uint32_t tail;
	if (resp.type == NVMEV_MQSIM_MSG_VUC_REPLY) {
		ring = &shm->vuc_resp_ring;
		entries = shm->vuc_resp_entries;
		mask = NVMEV_MQSIM_VUC_RING_MASK;
	} else {
		ring = &shm->resp_ring;
		entries = shm->resp_entries;
		mask = NVMEV_MQSIM_RING_MASK;
	}

	if (ring_full(*ring)) {
		store_u32(&ring->dropped, load_u32(&ring->dropped) + 1);
		return false;
	}

	tail = load_u32(&ring->tail);
	memcpy(&entries[tail & mask], &resp, sizeof(resp));
	__atomic_thread_fence(__ATOMIC_RELEASE);
	store_u32(&ring->tail, tail + 1);
	return true;
}

static bool msg_valid(const nvmev_mqsim_io_msg& msg)
{
	return msg.magic == NVMEV_MQSIM_MAGIC &&
	       msg.version == NVMEV_MQSIM_VERSION &&
	       (msg.type == NVMEV_MQSIM_MSG_IO_REQUEST ||
		msg.type == NVMEV_MQSIM_MSG_VUC_REQUEST);
}

static void print_usage(const char *prog)
{
	fprintf(stderr,
		"Usage: %s [--model fixed|online|native] [--ssd-config PATH] [--preload-extents PATH] [--vuc-neighbor-image PATH --vuc-vector-image PATH --vuc-node-map PATH --vuc-vector-map PATH --vuc-neighbor-lba-map PATH --vuc-vector-lba-map PATH --vuc-max-async-reads N --vuc-idma-units N --vuc-dbuf-capacity-pages N --vuc-parser-units N --vuc-input-dma-ns-per-4k N --vuc-idma-ns-per-4k N --vuc-parse-ns-per-id N --vuc-dsp-ns-per-component N --vuc-dsp-units N --vuc-output-dma-ns-per-4k N --vuc-idma-setup-ns N --vuc-ecc-ns-per-4k N --vuc-dsp-setup-ns-per-page N --vuc-candidate-update-ns-per-node N --vuc-dlm-capacity-pages N --vuc-trace PATH]\n",
		prog);
}

static void record_pop_delay(const nvmev_mqsim_io_msg& req)
{
	uint64_t pop_wall_ns = monotonic_now_ns();

	if (req.kernel_submit_wall_ns > 0 && pop_wall_ns >= req.kernel_submit_wall_ns) {
		uint64_t pop_delay_ns = pop_wall_ns - req.kernel_submit_wall_ns;

		daemon_timing_stats.pop_delay_count++;
		daemon_timing_stats.pop_delay_total_ns += pop_delay_ns;
		update_max_u64(daemon_timing_stats.pop_delay_max_ns, pop_delay_ns);
	}
}

static bool handle_request(nvmev_mqsim_shm *shm,
			   MQSimNativeOnlineModel& native_online_model,
			   MQSimVUCService *vuc_service,
			   uint64_t *vuc_available_time_ns,
			   const nvmev_mqsim_io_msg& req,
			   bool *produced_response)
{
	nvmev_mqsim_io_msg reply;
	if (req.type == NVMEV_MQSIM_MSG_VUC_REQUEST) {
		reply = req;
		reply.type = NVMEV_MQSIM_MSG_VUC_REPLY;
		reply.status = NVMEV_VUC_INVALID_ARGUMENT;
		if (req.vuc_slot >= NVMEV_MQSIM_VUC_SLOT_COUNT ||
		    req.payload_bytes > NVMEV_MQSIM_VUC_SLOT_BYTES)
			goto vuc_reply;
		if (req.vuc_opcode == NVMEV_VUC_OPCODE_EXPAND_AND_FILTER_BATCH &&
		    vuc_result_cache.size() >= NVMEV_MQSIM_VUC_SLOT_COUNT &&
		    vuc_result_cache.find(std::make_pair(req.query_id, req.batch_id)) ==
			vuc_result_cache.end()) {
			reply.status = NVMEV_VUC_RESOURCE_BUSY;
			goto vuc_reply;
		}
		if (req.vuc_opcode == NVMEV_VUC_OPCODE_FETCH_BATCH_RESULT) {
			auto result = vuc_result_cache.find({req.query_id, req.batch_id});
			if (result != vuc_result_cache.end() &&
			    result->second.size() <= req.payload_bytes) {
				auto *slot = shm->vuc_slots[req.vuc_slot].payload;
				std::memcpy(slot, result->second.data(), result->second.size());
				reply.status = NVMEV_VUC_SUCCESS;
				reply.response_bytes = (uint32_t)result->second.size();
				reply.latency_ns = 0;
				vuc_result_cache.erase(result);
			} else if (result != vuc_result_cache.end()) {
				reply.status = NVMEV_VUC_RESULT_OVERFLOW;
				reply.response_bytes = (uint32_t)result->second.size();
			}
		} else if (vuc_service && vuc_available_time_ns &&
		    req.payload_bytes >= sizeof(nvmev_vuc_expand_filter_request) &&
		    req.vuc_opcode == NVMEV_VUC_OPCODE_EXPAND_AND_FILTER_BATCH) {
			auto *slot = shm->vuc_slots[req.vuc_slot].payload;
			nvmev_vuc_expand_filter_request request = {};
			std::memcpy(&request, slot, sizeof(request));
			uint64_t arrival_ns = request.submit_time_ns;
			if (arrival_ns == 0)
				arrival_ns = native_online_model.simulated_time_ns();
			uint64_t start_ns = std::max(arrival_ns, *vuc_available_time_ns);
			start_ns = std::max(start_ns, native_online_model.simulated_time_ns());
			uint64_t finish_ns = start_ns;
			nvmev_vuc_completion completion = {};
			try {
				completion = vuc_service->execute(request, slot,
							  NVMEV_MQSIM_VUC_SLOT_BYTES,
							  start_ns, &finish_ns);
			} catch (const std::exception& ex) {
				fprintf(stderr, "MQSim VUC batch failed: %s\n", ex.what());
				completion.abi_version = NVMEV_VUC_ABI_VERSION;
				completion.status = NVMEV_VUC_RESOURCE_BUSY;
				completion.query_id = request.query_id;
				completion.batch_id = request.batch_id;
			}
			completion.c3_queue_wait_ns = start_ns - arrival_ns;
			completion.completion_time_ns = finish_ns;
			if (completion.result_valid &&
			    request.neighbor_ids_ref <= NVMEV_MQSIM_VUC_SLOT_BYTES &&
			    (uint64_t)completion.discovered_neighbor_count * sizeof(uint32_t) <=
				    NVMEV_MQSIM_VUC_SLOT_BYTES - request.neighbor_ids_ref &&
			    request.candidates_ref <= NVMEV_MQSIM_VUC_SLOT_BYTES &&
			    (uint64_t)completion.candidate_count * sizeof(nvmev_vuc_candidate) <=
				    NVMEV_MQSIM_VUC_SLOT_BYTES - request.candidates_ref) {
				nvmev_vuc_pcie_result_header header = {};
				header.completion = completion;
				header.total_bytes = sizeof(header) +
					(uint32_t)completion.output_bytes;
				if (header.total_bytes > NVMEV_MQSIM_VUC_SLOT_BYTES) {
					completion.status = NVMEV_VUC_RESULT_OVERFLOW;
					completion.result_valid = 0;
				} else {
				std::vector<uint8_t> serialized(header.total_bytes);
				std::memcpy(serialized.data(), &header, sizeof(header));
				uint8_t *out = serialized.data() + sizeof(header);
				const size_t neighbor_bytes =
					(size_t)completion.discovered_neighbor_count * sizeof(uint32_t);
				const size_t candidate_bytes =
					(size_t)completion.candidate_count * sizeof(nvmev_vuc_candidate);
				std::memcpy(out, slot + request.neighbor_ids_ref, neighbor_bytes);
				std::memcpy(out + neighbor_bytes, slot + request.candidates_ref,
					    candidate_bytes);
				const uint64_t query_id = request.query_id;
				const uint64_t batch_id = request.batch_id;
				vuc_result_cache[std::make_pair(query_id, batch_id)] = std::move(serialized);
				}
			} else if (completion.result_valid) {
				completion.status = NVMEV_VUC_INVALID_BUFFER;
				completion.result_valid = 0;
			}
			std::memcpy(slot, &completion, sizeof(completion));
			reply.status = (int32_t)completion.status;
			reply.latency_ns = finish_ns >= arrival_ns ? finish_ns - arrival_ns : 0;
			reply.response_bytes = sizeof(completion);
			*vuc_available_time_ns = finish_ns;
		}
	vuc_reply:
		if (!push_response(shm, reply))
			return false;
		if (produced_response)
			*produced_response = true;
		return true;
	}

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		uint64_t submit_begin_ns;
		uint64_t submit_ns;

		record_pop_delay(req);
		uint64_t ahead_ns = Simulator->Time() > req.submit_time_ns ?
			Simulator->Time() - req.submit_time_ns : 0;
		daemon_timing_stats.injection_count++;
		if (ahead_ns > 0)
			daemon_timing_stats.late_injection_count++;
		daemon_timing_stats.injection_ahead_total_ns += ahead_ns;
		update_max_u64(daemon_timing_stats.injection_ahead_max_ns, ahead_ns);
		submit_begin_ns = monotonic_now_ns();
		native_online_model.submit_request(req);
		submit_ns = monotonic_now_ns() - submit_begin_ns;
		daemon_timing_stats.submit_count++;
		daemon_timing_stats.submit_total_ns += submit_ns;
		update_max_u64(daemon_timing_stats.submit_max_ns, submit_ns);
		return true;
	}

	reply = req;
	reply.type = NVMEV_MQSIM_MSG_IO_REPLY;
	reply.latency_ns = estimate_latency_ns(req);
	reply.status = 0;
	if (!push_response(shm, reply)) {
		fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
			(unsigned long long)req.request_id);
		return false;
	}
	if (produced_response)
		*produced_response = true;
	return true;
}

static uint64_t drain_requests(nvmev_mqsim_shm *shm,
			       MQSimNativeOnlineModel& native_online_model,
			       MQSimVUCService *vuc_service,
			       uint64_t *vuc_available_time_ns,
			       bool *produced_response,
			       uint64_t max_requests)
{
	uint64_t handled = 0;

	while (max_requests == 0 || handled < max_requests) {
		nvmev_mqsim_io_msg req;

		if (!pop_request(shm, &req))
			break;
		if (!msg_valid(req))
			continue;
		if (handle_request(shm, native_online_model, vuc_service,
				   vuc_available_time_ns, req, produced_response))
			handled++;
	}
	return handled;
}

static uint64_t coalesce_requests(nvmev_mqsim_shm *shm,
				  MQSimNativeOnlineModel& native_online_model,
				  MQSimVUCService *vuc_service,
				  uint64_t *vuc_available_time_ns,
				  bool *produced_response,
				  uint64_t already_handled)
{
	uint64_t total_handled = already_handled;
	uint64_t start_ns;
	uint64_t deadline_ns;

	if (timing_model != TimingModel::NATIVE_ONLINE ||
	    request_coalesce_us == 0 || request_coalesce_max == 0 ||
	    already_handled >= request_coalesce_max)
		return total_handled;
	if (already_handled == 0 && !native_online_model.has_pending_requests())
		return total_handled;

	start_ns = monotonic_now_ns();
	deadline_ns = start_ns + request_coalesce_us * 1000;
	while (total_handled < request_coalesce_max) {
		uint64_t newly_handled =
			drain_requests(shm, native_online_model, vuc_service,
				       vuc_available_time_ns, produced_response,
				       request_coalesce_max - total_handled);

		total_handled += newly_handled;
		if (total_handled >= request_coalesce_max)
			break;
		if (monotonic_now_ns() >= deadline_ns)
			break;
		if (newly_handled == 0)
			sched_yield();
	}

	{
		uint64_t waited_ns = monotonic_now_ns() - start_ns;

		daemon_timing_stats.coalesce_round_count++;
		daemon_timing_stats.coalesce_wait_total_ns += waited_ns;
		update_max_u64(daemon_timing_stats.coalesce_wait_max_ns, waited_ns);
		daemon_timing_stats.coalesce_requests_total += total_handled;
		update_max_u64(daemon_timing_stats.coalesce_requests_max, total_handled);
	}
	return total_handled;
}

static bool parse_u64_arg(int argc, char **argv, int *idx, uint64_t *value)
{
	if (*idx + 1 >= argc)
		return false;
	*value = strtoull(argv[++(*idx)], NULL, 10);
	return true;
}

static std::vector<std::string> split_csv_line(const std::string& line)
{
	std::vector<std::string> fields;
	size_t start = 0;

	while (start <= line.size()) {
		size_t pos = line.find(',', start);
		if (pos == std::string::npos) {
			fields.push_back(line.substr(start));
			break;
		}
		fields.push_back(line.substr(start, pos - start));
		start = pos + 1;
	}
	return fields;
}

static bool load_preload_extents(const char *path, MQSimNativeOnlineModel& model)
{
	FILE *file = fopen(path, "r");
	char line[4096];
	uint64_t extent_count = 0;
	uint64_t allocated_pages = 0;

	if (!file) {
		perror("open preload extents");
		return false;
	}

	while (fgets(line, sizeof(line), file)) {
		std::string text(line);
		std::vector<std::string> fields;
		uint64_t slba;
		uint64_t nlb;

		while (!text.empty() && (text[text.size() - 1] == '\n' || text[text.size() - 1] == '\r'))
			text.erase(text.size() - 1);
		if (text.empty() || text[0] == '#')
			continue;

		fields = split_csv_line(text);
		if (fields.size() == 2) {
			slba = strtoull(fields[0].c_str(), NULL, 10);
			nlb = strtoull(fields[1].c_str(), NULL, 10);
		} else if (fields.size() >= 4) {
			slba = strtoull(fields[fields.size() - 2].c_str(), NULL, 10);
			nlb = strtoull(fields[fields.size() - 1].c_str(), NULL, 10);
		} else {
			fprintf(stderr, "Skipping malformed preload extent line: %s\n", text.c_str());
			continue;
		}

		if (nlb == 0)
			continue;
		allocated_pages += model.preload_write(slba, (uint32_t)nlb);
		extent_count++;
	}

	fclose(file);
	printf("MQSim native preload extents loaded. extents=%" PRIu64 " allocated_pages=%" PRIu64 " source=%s\n",
	       extent_count, allocated_pages, path);
	return true;
}

int main(int argc, char **argv)
{
	int fd;
	nvmev_mqsim_shm *shm;
	long page_size = sysconf(_SC_PAGESIZE);
	size_t shm_size;
	struct pollfd pollfds[1] = {};
	MQSimNativeOnlineModel native_online_model;
	std::unique_ptr<MQSimVUCService> vuc_service;
	uint64_t daemon_request_count = 0;
	uint64_t daemon_reply_count = 0;
	uint64_t next_stats_at = 0;
	uint64_t vuc_available_time_ns = 0;

	if (page_size <= 0) {
		perror("sysconf(_SC_PAGESIZE)");
		return 1;
	}
	shm_size = (sizeof(nvmev_mqsim_shm) + page_size - 1) & ~(page_size - 1);

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--read-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &read_latency_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--write-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &write_latency_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--ctrl-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &controller_overhead_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--xfer-4k-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &transfer_per_4k_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--trace-first")) {
			if (!parse_u64_arg(argc, argv, &i, &trace_first_requests))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--stats-every-reqs")) {
			if (!parse_u64_arg(argc, argv, &i, &stats_every_reqs))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--stats-top")) {
			if (!parse_u64_arg(argc, argv, &i, &stats_top_n))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--completion-window-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &completion_window_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--request-coalesce-us")) {
			if (!parse_u64_arg(argc, argv, &i, &request_coalesce_us))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--request-coalesce-max")) {
			if (!parse_u64_arg(argc, argv, &i, &request_coalesce_max))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--event-stats")) {
			event_stats = true;
		} else if (!strcmp(argv[i], "--event-trace-first-events")) {
			if (!parse_u64_arg(argc, argv, &i, &event_trace_first_events))
				goto invalid_args;
			event_stats = true;
		} else if (!strcmp(argv[i], "--stats-full")) {
			stats_full = true;
		} else if (!strcmp(argv[i], "--ssd-config")) {
			if (i + 1 >= argc)
				goto invalid_args;
			ssd_config_path = argv[++i];
		} else if (!strcmp(argv[i], "--preload-extents")) {
			if (i + 1 >= argc)
				goto invalid_args;
			preload_extents_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-trace")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_trace_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-neighbor-image")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_neighbor_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-vector-image")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_vector_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-vector-map")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_vector_map_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-node-map")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_node_map_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-neighbor-lba-map")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_neighbor_lba_map_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-vector-lba-map")) {
			if (i + 1 >= argc) goto invalid_args;
			vuc_vector_lba_map_path = argv[++i];
		} else if (!strcmp(argv[i], "--vuc-dlm-capacity-pages")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_dlm_capacity_pages)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-idma-setup-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_idma_setup_ns)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-ecc-ns-per-4k")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_ecc_ns_per_4k)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-candidate-update-ns-per-node")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_candidate_update_ns_per_node)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-dsp-setup-ns-per-page")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_dsp_setup_ns_per_page)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-max-async-reads")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_max_async_reads)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-idma-units")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_idma_units)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-dbuf-capacity-pages")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_dbuf_capacity_pages)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-parser-units")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_parser_units)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-input-dma-ns-per-4k")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_input_dma_ns_per_4k)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-idma-ns-per-4k") ||
			   !strcmp(argv[i], "--vuc-dbuf-ns-per-4k")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_idma_ns_per_4k)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-parse-ns-per-id")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_parse_ns_per_id)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-dsp-ns-per-component")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_dsp_ns_per_component)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-dsp-units")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_dsp_units)) goto invalid_args;
		} else if (!strcmp(argv[i], "--vuc-output-dma-ns-per-4k")) {
			if (!parse_u64_arg(argc, argv, &i, &vuc_output_dma_ns_per_4k)) goto invalid_args;
		} else if (!strcmp(argv[i], "--model")) {
			if (i + 1 >= argc)
				goto invalid_args;
			const char *model = argv[++i];
			if (!strcmp(model, "online") || !strcmp(model, "native")) {
				timing_model = TimingModel::NATIVE_ONLINE;
			} else if (!strcmp(model, "fixed")) {
				timing_model = TimingModel::FIXED;
			} else {
				goto invalid_args;
			}
		} else {
			goto invalid_args;
		}
	}

	if (stats_every_reqs > 0)
		next_stats_at = stats_every_reqs;

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		std::string error;

		if (!native_online_model.initialize(ssd_config_path, &error)) {
			fprintf(stderr, "MQSim native online model init failed: %s\n", error.c_str());
			return 1;
		}

		if (event_stats)
			Simulator->Enable_event_stats(event_trace_first_events);

		if (preload_extents_path && !load_preload_extents(preload_extents_path, native_online_model))
			return 1;
	}

	if (!vuc_neighbor_path.empty()) {
		if (timing_model != TimingModel::NATIVE_ONLINE ||
		    vuc_neighbor_path.empty() || vuc_vector_path.empty() ||
		    vuc_node_map_path.empty() || vuc_neighbor_lba_map_path.empty() ||
		    vuc_vector_lba_map_path.empty() || !vuc_max_async_reads || vuc_max_async_reads > 16 ||
		    vuc_idma_units != 1 ||
		    !vuc_dbuf_capacity_pages || vuc_dbuf_capacity_pages > 32 ||
		    vuc_dlm_capacity_pages < 2 || vuc_dlm_capacity_pages > 32 ||
		    !vuc_parser_units || vuc_parser_units > UINT32_MAX ||
		    !vuc_dsp_units || vuc_dsp_units > UINT32_MAX) {
			fprintf(stderr, "VUC requires native mode, images/maps, depth 1..16, one shared DMA lane, DBUF 1..32 pages, DLM 2..32 pages\n");
			return 1;
		}
		VUCServiceConfig config;
		config.trace_path = vuc_trace_path;
		config.neighbor_path = vuc_neighbor_path;
		config.vector_path = vuc_vector_path;
		config.node_to_nbr_slot_path = vuc_node_map_path;
		config.node_to_vector_slot_path = vuc_vector_map_path;
		config.neighbor_page_lba_path = vuc_neighbor_lba_map_path;
		config.vector_page_lba_path = vuc_vector_lba_map_path;
		config.max_async_reads = (uint32_t)vuc_max_async_reads;
		config.idma_units = (uint32_t)vuc_idma_units;
		config.dbuf_capacity_pages = (uint32_t)vuc_dbuf_capacity_pages;
		config.parser_units = (uint32_t)vuc_parser_units;
		config.input_dma_ns_per_4k = vuc_input_dma_ns_per_4k;
		config.idma_ns_per_4k = vuc_idma_ns_per_4k;
		config.neighbor_parse_ns_per_id = vuc_parse_ns_per_id;
		config.dsp_ns_per_float_component = vuc_dsp_ns_per_component;
		config.dsp_units = (uint32_t)vuc_dsp_units;
		config.dlm_capacity_pages = vuc_dlm_capacity_pages;
		config.idma_setup_ns = vuc_idma_setup_ns;
		config.ecc_ns_per_4k = vuc_ecc_ns_per_4k;
		config.candidate_update_ns_per_node = vuc_candidate_update_ns_per_node;
		config.dsp_setup_ns_per_page = vuc_dsp_setup_ns_per_page;
		config.output_dma_ns_per_4k = vuc_output_dma_ns_per_4k;
		try {
			vuc_service.reset(new MQSimVUCService(native_online_model, config));
		} catch (const std::exception& ex) {
			fprintf(stderr, "VUC data/model initialization failed: %s\n", ex.what());
			return 1;
		}
		printf("VUC C3 service ready: transport=NVMeVirt-ring max_async_reads=%" PRIu64 " query/vector pages use separate LBA extents\n",
		       vuc_max_async_reads);
		printf("VUC assumed costs (not hardware measurements): input/output=%" PRIu64 "/%" PRIu64
		       " ns/4K, shared DMA setup=%" PRIu64 " ns + %" PRIu64 " ns/4K, ECC=%" PRIu64
		       " ns/4K, parser=%" PRIu64 " ns/ID, L2=%" PRIu64 " ns/component + %" PRIu64
		       " ns/page, update=%" PRIu64 " ns/node, DBUF/DLM=%" PRIu64 "/%" PRIu64 " pages\n",
		       vuc_input_dma_ns_per_4k, vuc_output_dma_ns_per_4k, vuc_idma_setup_ns,
		       vuc_idma_ns_per_4k, vuc_ecc_ns_per_4k, vuc_parse_ns_per_id,
		       vuc_dsp_ns_per_component, vuc_dsp_setup_ns_per_page,
		       vuc_candidate_update_ns_per_node, vuc_dbuf_capacity_pages, vuc_dlm_capacity_pages);
		vuc_available_time_ns = native_online_model.simulated_time_ns();
	}

	fd = open("/dev/" NVMEV_MQSIM_DEVICE_NAME, O_RDWR);
	if (fd < 0) {
		perror("open shared-memory IPC device");
		return 1;
	}

	shm = (nvmev_mqsim_shm *)mmap(NULL, shm_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (shm == MAP_FAILED) {
		perror("mmap shared-memory IPC");
		close(fd);
		return 1;
	}

	if (load_u32(&shm->magic) != NVMEV_MQSIM_MAGIC ||
	    load_u32(&shm->version) != NVMEV_MQSIM_VERSION ||
	    load_u32(&shm->ring_size) != NVMEV_MQSIM_RING_SIZE ||
	    load_u32(&shm->vuc_req_ring.size) != NVMEV_MQSIM_VUC_RING_SIZE ||
	    load_u32(&shm->vuc_resp_ring.size) != NVMEV_MQSIM_VUC_RING_SIZE) {
		fprintf(stderr, "Shared-memory IPC ABI mismatch\n");
		munmap(shm, shm_size);
		close(fd);
		return 1;
	}

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		printf("MQSim shared-memory IPC daemon connected. model=native config=%s %s\n",
		       ssd_config_path, native_online_model.describe().c_str());
	} else {
		printf("MQSim shared-memory IPC daemon connected. model=fixed read=%lu ns write=%lu ns ctrl=%lu ns xfer_4k=%lu ns\n",
		       read_latency_ns, write_latency_ns, controller_overhead_ns, transfer_per_4k_ns);
	}

	pollfds[0].fd = fd;
	pollfds[0].events = POLLIN;
	{
	struct sigaction action = {};
	action.sa_handler = handle_signal;
	sigemptyset(&action.sa_mask);
	sigaction(SIGUSR1, &action, NULL);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGTERM, &action, NULL);
	}
	printf("Daemon diagnostics ready: pid=%ld SIGUSR1=snapshot SIGINT/SIGTERM=final-snapshot-and-stop; coalesce_us=%" PRIu64 " coalesce_max=%" PRIu64 " completion_window_ns=%" PRIu64 "\n",
	       (long)getpid(), request_coalesce_us, request_coalesce_max, completion_window_ns);
	fflush(stdout);

	while (true) {
		if (snapshot_requested || stop_requested) {
			snapshot_requested = 0;
			printf("Diagnostic snapshot: wall_ns=%" PRIu64 " sim_ns=%llu final=%d (cumulative counters)\n",
			       monotonic_now_ns(), (unsigned long long)Simulator->Time(), (int)stop_requested);
			if (timing_model == TimingModel::NATIVE_ONLINE) {
				if (stats_full)
					native_online_model.print_stats(stdout);
				else
					native_online_model.print_compact_stats(stdout, stats_top_n);
			}
			print_daemon_timing_stats(stdout);
			fflush(stdout);
			if (stop_requested)
				break;
		}
		int ready;
		bool produced_response = false;
		uint64_t handled_this_loop = 0;

		handled_this_loop = drain_requests(shm, native_online_model, vuc_service.get(),
						   &vuc_available_time_ns,
						   &produced_response, 0);
		handled_this_loop = coalesce_requests(shm, native_online_model, vuc_service.get(),
						      &vuc_available_time_ns,
						      &produced_response,
						      handled_this_loop);
		daemon_request_count += handled_this_loop;

		while (timing_model != TimingModel::FIXED && next_stats_at > 0 &&
		       daemon_request_count >= next_stats_at) {
				if (stats_full)
					native_online_model.print_stats(stdout);
				else
					native_online_model.print_compact_stats(stdout, stats_top_n);
				print_daemon_timing_stats(stdout);
				if (event_stats)
					Simulator->Print_event_stats(stdout, stats_top_n);
				next_stats_at += stats_every_reqs;
		}

		if (timing_model == TimingModel::NATIVE_ONLINE) {
			nvmev_mqsim_io_msg reply;
			std::string trace;

			if (native_online_model.has_pending_requests()) {
				uint64_t run_begin_ns = monotonic_now_ns();
				size_t pending_before = native_online_model.pending_count();
				sim_time_type sim_advanced_ns = 0;
				size_t replies_generated =
					native_online_model.run_completion_batch(
						(sim_time_type)completion_window_ns,
						&sim_advanced_ns);
				size_t pending_after = native_online_model.pending_count();
				uint64_t run_ns = monotonic_now_ns() - run_begin_ns;

				daemon_timing_stats.run_completion_count++;
				daemon_timing_stats.run_completion_total_ns += run_ns;
				update_max_u64(daemon_timing_stats.run_completion_max_ns, run_ns);
				daemon_timing_stats.run_pending_before_total += pending_before;
				update_max_u64(daemon_timing_stats.run_pending_before_max, pending_before);
				daemon_timing_stats.run_pending_after_total += pending_after;
				update_max_u64(daemon_timing_stats.run_pending_after_max, pending_after);
				daemon_timing_stats.run_replies_total += replies_generated;
				update_max_u64(daemon_timing_stats.run_replies_max, replies_generated);
				daemon_timing_stats.run_sim_advanced_total_ns += sim_advanced_ns;
				update_max_u64(daemon_timing_stats.run_sim_advanced_max_ns, sim_advanced_ns);

				if (replies_generated == 0) {
					fprintf(stderr, "MQSim native event queue drained with pending requests\n");
					break;
				}
			}

			while (native_online_model.pop_completed_reply(&reply, &trace)) {
				uint64_t push_begin_ns = monotonic_now_ns();
				bool pushed = push_response(shm, reply);
				uint64_t push_ns = monotonic_now_ns() - push_begin_ns;

				daemon_timing_stats.push_response_count++;
				daemon_timing_stats.push_response_total_ns += push_ns;
				update_max_u64(daemon_timing_stats.push_response_max_ns, push_ns);

				if (!pushed) {
					fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
						(unsigned long long)reply.request_id);
					continue;
				}
				daemon_reply_count++;
				if (trace_first_requests > 0 && daemon_reply_count <= trace_first_requests)
					printf("MQSim online trace: %s\n", trace.c_str());
				produced_response = true;
			}
		}

		if (handled_this_loop > 0)
			fflush(stdout);

		if (produced_response && ioctl(fd, NVMEV_MQSIM_IOCTL_NOTIFY_RESP) < 0) {
			perror("ioctl notify response");
			break;
		}

		if (timing_model == TimingModel::NATIVE_ONLINE && native_online_model.has_pending_requests())
			continue;

		// Bounded wait also handles a signal arriving just before poll().
		ready = poll(pollfds, 1, 100);
		if (ready < 0) {
			if (errno == EINTR)
				continue;
			perror("poll");
			break;
		}

	}

	munmap(shm, shm_size);
	close(fd);
	return 0;

invalid_args:
	print_usage(argv[0]);
	return 1;
}
