#include "vuc_service.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <queue>
#include <set>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace {
constexpr uint32_t PAGE_BYTES = 4096;
constexpr uint32_t SECTORS_PER_PAGE = PAGE_BYTES / 512;
constexpr uint32_t NBR_RECORD_BYTES = 4 * (64 + 1);
constexpr uint32_t VECTOR_BYTES_PER_NODE = 128 * sizeof(float);
uint32_t read_u32(const uint8_t *p);

std::vector<uint8_t> read_file(const std::string& path)
{
	std::ifstream in(path.c_str(), std::ios::binary | std::ios::ate);
	if (!in)
		throw std::runtime_error("cannot open VUC data file: " + path);
	std::streamsize size = in.tellg();
	if (size < 0)
		throw std::runtime_error("cannot size VUC data file: " + path);
	std::vector<uint8_t> bytes((size_t)size);
	in.seekg(0, std::ios::beg);
	if (size && !in.read((char *)bytes.data(), size))
		throw std::runtime_error("cannot read VUC data file: " + path);
	return bytes;
}

std::vector<uint64_t> read_lba_map(const std::string& path)
{
	std::vector<uint8_t> bytes = read_file(path);
	if (bytes.size() < 8)
		throw std::runtime_error("truncated page-to-LBA map: " + path);
	uint32_t count = read_u32(bytes.data());
	uint32_t dim = read_u32(bytes.data() + 4);
	if (dim != 1 || bytes.size() != 8 + (size_t)count * sizeof(uint64_t))
		throw std::runtime_error("invalid page-to-LBA map: " + path);
	std::vector<uint64_t> values(count);
	std::memcpy(values.data(), bytes.data() + 8, values.size() * sizeof(uint64_t));
	return values;
}

uint32_t read_u32(const uint8_t *p)
{
	uint32_t value;
	std::memcpy(&value, p, sizeof(value));
	return value;
}

uint64_t page_count(uint64_t value, uint64_t alignment)
{
	return value / alignment + (value % alignment != 0);
}
}

MQSimVUCService::MQSimVUCService(MQSimNativeOnlineModel& model,
				 const VUCServiceConfig& config)
	: model_(model), config_(config), node_count_(0), dimension_(0), max_degree_(0),
	  nodes_per_nbr_page_(0), vectors_per_page_(0), next_internal_request_id_(1ULL << 60)
{
	neighbors_ = read_file(config_.neighbor_path);
	vectors_ = read_file(config_.vector_path);
	std::vector<uint8_t> map = read_file(config_.node_to_nbr_slot_path);
	neighbor_page_lba_ = read_lba_map(config_.neighbor_page_lba_path);
	vector_page_lba_ = read_lba_map(config_.vector_page_lba_path);
	if (neighbors_.size() < PAGE_BYTES || vectors_.size() < PAGE_BYTES || map.size() < 8)
		throw std::runtime_error("truncated NBR/vector/map image");
	if (std::memcmp(neighbors_.data(), "NBRONLY1", 8) != 0 ||
	    std::memcmp(vectors_.data(), "VECF32V1", 8) != 0)
		throw std::runtime_error("unsupported NBR/vector image header");
	node_count_ = read_u32(neighbors_.data() + 12);
	dimension_ = read_u32(neighbors_.data() + 16);
	max_degree_ = read_u32(neighbors_.data() + 20);
	if (node_count_ == 0 || dimension_ != 128 || max_degree_ > 64)
		throw std::runtime_error("invalid SIFT1M NBR image metadata");
	if (read_u32(vectors_.data() + 12) != node_count_ ||
	    read_u32(vectors_.data() + 16) != dimension_)
		throw std::runtime_error("NBR/vector node count or dimension mismatch");
	uint32_t map_count = read_u32(map.data());
	uint32_t map_dim = read_u32(map.data() + 4);
	if (map_count != node_count_ || map_dim != 1 || map.size() != 8 + (size_t)node_count_ * 4)
		throw std::runtime_error("invalid node-to-NBR-slot map");
	node_to_nbr_slot_.resize(node_count_);
	std::memcpy(node_to_nbr_slot_.data(), map.data() + 8, (size_t)node_count_ * 4);
	node_to_vector_slot_.resize(node_count_);
	for (uint32_t i = 0; i < node_count_; ++i) node_to_vector_slot_[i] = i;
	if (!config_.node_to_vector_slot_path.empty()) {
		auto vecmap = read_file(config_.node_to_vector_slot_path);
		if (vecmap.size() != 8 + (size_t)node_count_ * 4 ||
		    read_u32(vecmap.data()) != node_count_ || read_u32(vecmap.data() + 4) != 1)
			throw std::runtime_error("invalid node-to-vector-slot map");
		std::memcpy(node_to_vector_slot_.data(), vecmap.data() + 8, node_count_ * 4);
	}
	for (const auto* placement : {&node_to_nbr_slot_, &node_to_vector_slot_}) {
		std::vector<bool> seen(node_count_, false);
		for (uint32_t slot : *placement) {
			if (slot >= node_count_ || seen[slot]) throw std::runtime_error("placement map is not a permutation");
			seen[slot] = true;
		}
	}
	nodes_per_nbr_page_ = PAGE_BYTES / NBR_RECORD_BYTES;
	vectors_per_page_ = PAGE_BYTES / VECTOR_BYTES_PER_NODE;
	if (neighbors_.size() != (size_t)(1 + (node_count_ + nodes_per_nbr_page_ - 1) /
							 nodes_per_nbr_page_) * PAGE_BYTES ||
	    vectors_.size() != (size_t)PAGE_BYTES + page_count((size_t)node_count_ * VECTOR_BYTES_PER_NODE, PAGE_BYTES) * PAGE_BYTES)
		throw std::runtime_error("NBR/vector image size does not match declared fixed-width layout");
	if (neighbor_page_lba_.size() != neighbors_.size() / PAGE_BYTES ||
	    vector_page_lba_.size() != vectors_.size() / PAGE_BYTES)
		throw std::runtime_error("page-to-LBA maps do not cover the entire NBR/vector image");
}

bool MQSimVUCService::range_valid(uint64_t offset, uint64_t bytes,
				  size_t aperture_bytes) const
{
	return offset <= aperture_bytes && bytes <= aperture_bytes - (size_t)offset;
}

nvmev_vuc_completion MQSimVUCService::execute(
	const nvmev_vuc_expand_filter_request& request, void *shared_buffer,
	size_t shared_buffer_bytes, uint64_t c3_start_time_ns,
	uint64_t *c3_finish_time_ns)
{
	const bool raw_pages = request.reserved0 == NVMEV_VUC_PHASE_NBR_PAGES;
	const bool rerank = request.reserved0 == NVMEV_VUC_PHASE_RERANK;
	nvmev_vuc_completion done{};
	done.abi_version = NVMEV_VUC_ABI_VERSION;
	done.query_id = request.query_id;
	done.batch_id = request.batch_id;
	if (c3_finish_time_ns)
		*c3_finish_time_ns = c3_start_time_ns;
	if (shared_buffer == nullptr || request.abi_version != NVMEV_VUC_ABI_VERSION ||
	    request.opcode != NVMEV_VUC_OPCODE_EXPAND_AND_FILTER_BATCH ||
	    request.threshold_format != NVMEV_VUC_THRESHOLD_FP32 || request.node_count == 0 ||
	    request.node_count > NVMEV_VUC_MAX_BATCH_NODES ||
	    (rerank && config_.node_to_vector_slot_path.empty()) ||
	    request.reserved0 > NVMEV_VUC_PHASE_RERANK || request.top_k == 0 || request.timeout_ms == 0 || std::isnan(request.threshold_fp32) ||
		request.node_list_bytes != (uint64_t)request.node_count * sizeof(uint32_t) ||
	    request.query_bytes != (raw_pages ? 0 : (uint64_t)dimension_ * sizeof(float)) ||
		config_.max_async_reads == 0 || config_.max_async_reads > 16 ||
		config_.idma_units != 1 || config_.dbuf_capacity_pages == 0 ||
		config_.dbuf_capacity_pages > 32 || config_.dlm_capacity_pages < 2 ||
		config_.dlm_capacity_pages > 32 ||
		config_.parser_units == 0 || config_.dsp_units == 0) {
		done.status = NVMEV_VUC_INVALID_ARGUMENT;
		return done;
	}
	if (!range_valid(request.node_list_ref, request.node_list_bytes, shared_buffer_bytes) ||
	    !range_valid(request.query_ref, request.query_bytes, shared_buffer_bytes) ||
	    !range_valid(request.neighbor_ids_ref,
			 (uint64_t)request.neighbor_ids_capacity * sizeof(uint32_t), shared_buffer_bytes) ||
	    !range_valid(request.candidates_ref,
			 (uint64_t)request.candidate_capacity * sizeof(nvmev_vuc_candidate), shared_buffer_bytes)) {
		done.status = NVMEV_VUC_INVALID_BUFFER;
		return done;
	}
	if ((request.node_list_ref | request.query_ref | request.neighbor_ids_ref |
	     request.candidates_ref) & (alignof(uint32_t) - 1)) {
		done.status = NVMEV_VUC_INVALID_BUFFER;
		return done;
	}
	uint8_t *aperture = (uint8_t *)shared_buffer;
	const uint32_t *expand_ids = (const uint32_t *)(aperture + request.node_list_ref);
	const float *query = (const float *)(aperture + request.query_ref);
	uint32_t *neighbor_output = (uint32_t *)(aperture + request.neighbor_ids_ref);
	nvmev_vuc_candidate *candidate_output =
		(nvmev_vuc_candidate *)(aperture + request.candidates_ref);
	for (uint32_t i = 0; i < request.node_count; ++i) {
		if (expand_ids[i] >= node_count_) {
			done.status = NVMEV_VUC_INVALID_NODE;
			return done;
		}
	}
	done.input_bytes = sizeof(request) + request.node_list_bytes + request.query_bytes;
	if (config_.input_dma_ns_per_4k)
		done.input_dma_ns = page_count(done.input_bytes, PAGE_BYTES) *
			config_.input_dma_ns_per_4k;

	std::unordered_map<uint32_t, uint32_t> neighbor_source;
	std::unordered_map<uint64_t, uint64_t> edges_by_nbr_page;
	std::unordered_map<uint64_t, std::vector<uint32_t>> expanded_by_nbr_page;
	std::set<uint64_t> nbr_pages_set;
	/* Build the page dependency schedule from the static backing image. The
	 * runtime neighbor list and exact scores below are still consumed from the
	 * DBuf copies after the modeled NAND/iDMA/parser events complete. */
	for (uint32_t i = 0; i < request.node_count; ++i) {
		if (rerank) break;
		uint32_t node_id = expand_ids[i];
		uint32_t slot = node_to_nbr_slot_[node_id];
		if (slot >= node_count_) {
			done.status = NVMEV_VUC_INVALID_NODE;
			return done;
		}
		const uint64_t nbr_page = 1 + slot / nodes_per_nbr_page_;
		nbr_pages_set.insert(nbr_page);
		if (raw_pages) continue;
		expanded_by_nbr_page[nbr_page].push_back(node_id);
		uint64_t offset = PAGE_BYTES +
			(uint64_t)(slot / nodes_per_nbr_page_) * PAGE_BYTES +
			(uint64_t)(slot % nodes_per_nbr_page_) * NBR_RECORD_BYTES;
		uint32_t degree = read_u32(neighbors_.data() + offset);
		if (degree > max_degree_) {
			done.status = NVMEV_VUC_INVALID_ARGUMENT;
			return done;
		}
		const uint8_t *ids = neighbors_.data() + offset + 4;
		for (uint32_t j = 0; j < degree; ++j) {
			uint32_t neighbor = read_u32(ids + j * 4);
			if (neighbor >= node_count_) {
				done.status = NVMEV_VUC_INVALID_NODE;
				return done;
			}
			neighbor_source.emplace(neighbor, node_id);
			++edges_by_nbr_page[nbr_page];
		}
	}
	if ((raw_pages && (nbr_pages_set.size() > NVMEV_VUC_MAX_NBR_PAGES ||
	    nbr_pages_set.size() * NVMEV_VUC_NBR_PAGE_WORDS > request.neighbor_ids_capacity)) ||
	    neighbor_source.size() > request.neighbor_ids_capacity) {
		done.status = NVMEV_VUC_RESULT_OVERFLOW;
		done.result_valid = 0;
		return done;
	}
	std::unordered_map<uint32_t, uint32_t> score_source = neighbor_source;
	for (uint32_t i = 0; !raw_pages && i < request.node_count; ++i)
		score_source.emplace(expand_ids[i], expand_ids[i]);
	std::set<uint64_t> vector_page_set;
	std::unordered_map<uint64_t, std::vector<uint32_t>> score_ids_by_vector_page;
	std::unordered_map<uint64_t, std::set<uint64_t>> vector_page_dependencies;
	std::vector<nvmev_vuc_candidate> qualified;
	qualified.reserve(score_source.size());
	std::unordered_map<uint32_t, uint32_t> runtime_neighbor_source;
	for (const auto& item : score_source) {
		const uint64_t page = 1 + node_to_vector_slot_[item.first] / vectors_per_page_;
		vector_page_set.insert(page);
		score_ids_by_vector_page[page].push_back(item.first);
	}
	for (const auto& item : neighbor_source) {
		const uint32_t slot = node_to_nbr_slot_[item.second];
		const uint64_t nbr_page = 1 + slot / nodes_per_nbr_page_;
		const uint64_t vec_page = 1 + node_to_vector_slot_[item.first] / vectors_per_page_;
		vector_page_dependencies[vec_page].insert(nbr_page);
	}

	/* Model NAND -> DBUF -> parse, and vector-read -> DBUF -> DLM ->
	 * DSP -> DBUF -> candidate update. MQSim owns flash completion times; the small event
	 * scheduler below models finite internal resources and their queueing. */
	enum PageKind { NBR_PAGE, VECTOR_PAGE };
	struct ReadTask { PageKind kind; uint64_t page; uint64_t eligible_ns; uint64_t blocked_ns; };
	struct ActiveRead { PageKind kind; uint64_t page; uint64_t submitted_ns; uint32_t dbuf_slot; };
	enum StageKind { DBUF_NBR, DBUF_VECTOR, IDMA_VECTOR, PARSE_NBR, DSP_VECTOR, RETURN_VECTOR, UPDATE_VECTOR };
	struct StageEvent { uint64_t time_ns; StageKind kind; uint64_t page; uint32_t dbuf_slot; };
	struct LaterEvent {
		bool operator()(const StageEvent& a, const StageEvent& b) const {
			if (a.time_ns != b.time_ns) return a.time_ns > b.time_ns;
			return a.kind > b.kind;
		}
	};
	std::priority_queue<StageEvent, std::vector<StageEvent>, LaterEvent> stage_events;
	std::vector<ReadTask> read_queue;
	std::unordered_map<uint64_t, ActiveRead> active_reads;
	std::unordered_set<uint64_t> parsed_nbr_pages;
	std::unordered_set<uint64_t> scheduled_vector_pages;
	std::vector<uint64_t> idma_lane_free(config_.idma_units, 0);
	std::vector<uint64_t> parser_lane_free(config_.parser_units, 0);
	std::vector<uint64_t> dsp_lane_free(config_.dsp_units, 0);
	uint32_t dbuf_used = 0;
	uint32_t reads_inflight = 0;
	// One DLM page holds the shared query; each vector page is overwritten by
	// its score records after DSP, then returned through the SAME DMA channel.
	uint32_t dlm_used = 0;
	std::vector<float> dlm_query;
	if (!raw_pages) dlm_query.assign(query, query + dimension_);
	std::vector<uint32_t> raw_page_words;
	std::vector<uint8_t> dlm_slots((size_t)config_.dbuf_capacity_pages * PAGE_BYTES, 0);
	std::ofstream trace;
	if (!config_.trace_path.empty()) {
		std::ifstream existing(config_.trace_path.c_str(), std::ios::binary | std::ios::ate);
		const bool header = !existing || existing.tellg() == 0;
		trace.open(config_.trace_path.c_str(), std::ios::app);
		if (!trace) throw std::runtime_error("cannot open VUC timing trace");
		trace.exceptions(std::ios::badbit | std::ios::failbit);
		if (header) trace << "query_id,batch_id,kind,page,stage,ready_ns,start_ns,finish_ns,bytes\n";
	}
	auto record = [&](PageKind kind, uint64_t page, const char* stage,
			uint64_t ready, uint64_t start, uint64_t finish, uint64_t bytes) {
		if (trace.is_open()) trace << request.query_id << ',' << request.batch_id << ','
			<< (kind == NBR_PAGE ? "nbr" : "vector") << ',' << page << ',' << stage << ','
			<< ready << ',' << start << ',' << finish << ',' << bytes << '\n';
	};
	auto dma = [&](uint64_t ready, uint64_t page, uint32_t slot, StageKind kind) {
		uint64_t& free = idma_lane_free[0];
		const uint64_t start = std::max(ready, free);
		done.idma_queue_wait_ns += start - ready;
		free = start + config_.idma_setup_ns + config_.idma_ns_per_4k;
		done.vector_dma_ns += config_.idma_setup_ns + config_.idma_ns_per_4k;
		stage_events.push(StageEvent{free, kind, page, slot});
		record(VECTOR_PAGE, page, kind == IDMA_VECTOR ? "dbuf_to_dlm" : "dlm_to_dbuf",
			ready, start, free, PAGE_BYTES);
	};
	std::vector<uint8_t> dbuf_slots((size_t)config_.dbuf_capacity_pages * PAGE_BYTES, 0);
	std::vector<uint8_t> dbuf_slot_used(config_.dbuf_capacity_pages, 0);
	uint64_t current_ns = std::max(c3_start_time_ns, model_.simulated_time_ns()) + done.input_dma_ns;
	if (!raw_pages) {
	// Load the shared query into DLM once, using the same serialized engine.
	idma_lane_free[0] = current_ns + config_.idma_setup_ns + config_.idma_ns_per_4k;
	done.vector_dma_ns += config_.idma_setup_ns + config_.idma_ns_per_4k;
	record(VECTOR_PAGE, 0, "query_to_dlm", current_ns, current_ns, idma_lane_free[0], PAGE_BYTES);
	}
	uint64_t logical_finish_ns = current_ns;
	uint64_t dsp_tasks_pending = 0;
	uint64_t nbr_pages_pending = nbr_pages_set.size();
	uint64_t vec_pages_pending = vector_page_set.size();
	auto enqueue_read = [&](PageKind kind, uint64_t page, uint64_t eligible) {
		read_queue.push_back(ReadTask{kind, page, eligible, UINT64_MAX});
	};
	for (uint64_t page : nbr_pages_set)
		enqueue_read(NBR_PAGE, page, current_ns);
	for (uint64_t page : vector_page_set) {
		if (vector_page_dependencies[page].empty()) {
			scheduled_vector_pages.insert(page);
			enqueue_read(VECTOR_PAGE, page, current_ns);
		}
	}
	auto try_issue_reads = [&](uint64_t now_ns) {
		while (reads_inflight < config_.max_async_reads &&
		       dbuf_used < config_.dbuf_capacity_pages) {
			auto it = std::min_element(read_queue.begin(), read_queue.end(),
				[&](const ReadTask& a, const ReadTask& b) {
					const bool a_blocked = a.kind == VECTOR_PAGE && dlm_used >= config_.dlm_capacity_pages - 1;
					const bool b_blocked = b.kind == VECTOR_PAGE && dlm_used >= config_.dlm_capacity_pages - 1;
					if (a_blocked != b_blocked) return !a_blocked;
					return a.eligible_ns < b.eligible_ns;
				});
			if (it == read_queue.end() || it->eligible_ns > now_ns ||
			    (it->kind == VECTOR_PAGE && dlm_used >= config_.dlm_capacity_pages - 1))
				break;
			ReadTask task = *it;
			if (task.blocked_ns != UINT64_MAX)
				done.dbuf_wait_ns += now_ns - task.blocked_ns;
			read_queue.erase(it);
			uint32_t dbuf_slot = 0;
			while (dbuf_slot < dbuf_slot_used.size() && dbuf_slot_used[dbuf_slot])
				++dbuf_slot;
			if (dbuf_slot == dbuf_slot_used.size())
				throw std::runtime_error("DBuf accounting has no free page slot");
			dbuf_slot_used[dbuf_slot] = 1;
			const std::vector<uint64_t>& lbas = task.kind == NBR_PAGE ? neighbor_page_lba_ : vector_page_lba_;
			if (task.page >= lbas.size())
				throw std::runtime_error("page index exceeds LBA map");
			nvmev_mqsim_io_msg io{};
			io.request_id = next_internal_request_id_++;
			const uint64_t request_id = io.request_id;
			io.opcode = NVMEV_MQSIM_IO_READ;
			io.submit_time_ns = now_ns;
			io.slba = lbas[task.page];
			io.nlb = SECTORS_PER_PAGE;
			model_.submit_request(io);
			active_reads.emplace(request_id, ActiveRead{task.kind, task.page, now_ns, dbuf_slot});
			++dbuf_used;
			++reads_inflight;
			if (task.kind == VECTOR_PAGE) ++dlm_used;
		}
		if (dbuf_used >= config_.dbuf_capacity_pages) {
			for (ReadTask& task : read_queue)
				if (task.eligible_ns <= now_ns && task.blocked_ns == UINT64_MAX)
					task.blocked_ns = now_ns;
		}
	};
	try_issue_reads(current_ns);
	while (nbr_pages_pending || vec_pages_pending || dsp_tasks_pending || !active_reads.empty() ||
	       !stage_events.empty() || !read_queue.empty()) {
		uint64_t next_stage_ns = stage_events.empty() ? UINT64_MAX : stage_events.top().time_ns;
		uint64_t next_nand_ns = model_.has_pending_event() ? model_.next_event_time_ns() : UINT64_MAX;
		if (next_stage_ns == UINT64_MAX && next_nand_ns == UINT64_MAX) {
			/* Future eligible work can only be waiting for parser/DSP completion;
			 * a missing producer event indicates a malformed dependency graph. */
			if (!read_queue.empty())
				throw std::runtime_error("C3 pipeline stalled with unscheduled reads");
			break;
		}
		if (next_nand_ns <= next_stage_ns) {
			if (!model_.run_next_event())
				throw std::runtime_error("MQSim event queue drained during C3 read");
			current_ns = std::max(current_ns, model_.simulated_time_ns());
			nvmev_mqsim_io_msg reply{};
			while (model_.pop_completed_reply(&reply)) {
				auto found = active_reads.find(reply.request_id);
				if (found == active_reads.end())
					continue;
				ActiveRead task = found->second;
				active_reads.erase(found);
				const uint64_t nand_ready = reply.submit_time_ns + reply.latency_ns;
				const uint64_t ready = nand_ready + config_.ecc_ns_per_4k;
				// MQSim models translation, scheduling, flash array and NAND bus.
				// This separate assumed ECC tail ends at DBUF readiness, not DLM.
				if (task.kind == NBR_PAGE) done.neighbor_nand_ns += ready - task.submitted_ns;
				else done.vector_nand_ns += ready - task.submitted_ns;
				record(task.kind, task.page, "internal_read", task.submitted_ns, task.submitted_ns, ready, PAGE_BYTES);
				record(task.kind, task.page, "ecc", nand_ready, nand_ready, ready, PAGE_BYTES);
				stage_events.push(StageEvent{ready,
					task.kind == NBR_PAGE ? DBUF_NBR : DBUF_VECTOR, task.page, task.dbuf_slot});
			}
		} else {
			StageEvent event = stage_events.top();
			stage_events.pop();
			current_ns = event.time_ns;
			logical_finish_ns = std::max(logical_finish_ns, current_ns);
			if (event.kind == DBUF_NBR) {
				--reads_inflight;
				const uint64_t image_offset = event.page * PAGE_BYTES;
				if (image_offset + PAGE_BYTES > neighbors_.size())
					throw std::runtime_error("NBR DMA page exceeds its backing image");
				std::memcpy(dbuf_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES,
					neighbors_.data() + image_offset, PAGE_BYTES);

				if (raw_pages) {
					raw_page_words.push_back((uint32_t)event.page);
					raw_page_words.push_back(std::min(nodes_per_nbr_page_, node_count_ - (uint32_t)(event.page-1)*nodes_per_nbr_page_));
					const size_t offset = raw_page_words.size();
					raw_page_words.resize(offset + PAGE_BYTES / 4);
					std::memcpy(raw_page_words.data() + offset, dbuf_slots.data() + (size_t)event.dbuf_slot*PAGE_BYTES, PAGE_BYTES);
					dbuf_slot_used[event.dbuf_slot] = 0;
					--dbuf_used; --nbr_pages_pending;
					try_issue_reads(current_ns);
					continue;
				}
				const uint64_t work = edges_by_nbr_page[event.page] * config_.neighbor_parse_ns_per_id;
				auto lane = std::min_element(parser_lane_free.begin(), parser_lane_free.end());
				const uint64_t parse_start = std::max(current_ns, *lane);
				done.parser_queue_wait_ns += parse_start - current_ns;
				*lane = parse_start + work;
				done.neighbor_parse_ns += work;
				stage_events.push(StageEvent{*lane, PARSE_NBR, event.page, event.dbuf_slot});
				record(NBR_PAGE, event.page, "parse", current_ns, parse_start, *lane, PAGE_BYTES);
			} else if (event.kind == DBUF_VECTOR) {
				--reads_inflight;
				const uint64_t image_offset = event.page * PAGE_BYTES;
				if (image_offset + PAGE_BYTES > vectors_.size())
					throw std::runtime_error("vector DMA page exceeds its backing image");
				std::memcpy(dbuf_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES,
					vectors_.data() + image_offset, PAGE_BYTES);
				dma(current_ns, event.page, event.dbuf_slot, IDMA_VECTOR);
			} else if (event.kind == IDMA_VECTOR) {
				std::memcpy(dlm_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES,
					dbuf_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES, PAGE_BYTES);
				const uint64_t work = (uint64_t)score_ids_by_vector_page[event.page].size() *
					dimension_ * config_.dsp_ns_per_float_component + config_.dsp_setup_ns_per_page;
				auto lane = std::min_element(dsp_lane_free.begin(), dsp_lane_free.end());
				const uint64_t dsp_start = std::max(current_ns, *lane);
				done.dsp_queue_wait_ns += dsp_start - current_ns;
				done.dsp_compute_ns += work;
				*lane = dsp_start + work;
				++dsp_tasks_pending;
				stage_events.push(StageEvent{*lane, DSP_VECTOR, event.page, event.dbuf_slot});
				record(VECTOR_PAGE, event.page, "dsp", current_ns, dsp_start, *lane, PAGE_BYTES);
			} else if (event.kind == PARSE_NBR) {
				dbuf_slot_used[event.dbuf_slot] = 0;
				--dbuf_used;
				const uint8_t *page_data = dbuf_slots.data() +
					(size_t)event.dbuf_slot * PAGE_BYTES;
				for (uint32_t source_id : expanded_by_nbr_page[event.page]) {
					const uint32_t slot = node_to_nbr_slot_[source_id];
					const uint32_t record_in_page = slot % nodes_per_nbr_page_;
					const uint8_t *record = page_data +
						(size_t)record_in_page * NBR_RECORD_BYTES;
					const uint32_t degree = read_u32(record);
					if (degree > max_degree_)
						throw std::runtime_error("NBR DBuf record exceeds configured degree");
					for (uint32_t j = 0; j < degree; ++j) {
						const uint32_t neighbor = read_u32(record + 4 + j * sizeof(uint32_t));
						if (neighbor >= node_count_)
							throw std::runtime_error("NBR DBuf contains an invalid node ID");
						runtime_neighbor_source.emplace(neighbor, source_id);
					}
				}
				parsed_nbr_pages.insert(event.page);
				--nbr_pages_pending;
				for (uint64_t page : vector_page_set) {
					if (scheduled_vector_pages.count(page)) continue;
					bool ready = true;
					for (uint64_t dependency : vector_page_dependencies[page])
						if (!parsed_nbr_pages.count(dependency)) { ready = false; break; }
					if (ready) {
						scheduled_vector_pages.insert(page);
						enqueue_read(VECTOR_PAGE, page, current_ns);
					}
				}
			} else if (event.kind == DSP_VECTOR) {
				const uint8_t *page_data = dlm_slots.data() +
					(size_t)event.dbuf_slot * PAGE_BYTES;
				std::vector<nvmev_vuc_candidate> scores;
				for (uint32_t id : score_ids_by_vector_page[event.page]) {
					const uint32_t in_page = node_to_vector_slot_[id] % vectors_per_page_;
					const uint8_t *vector = page_data + (size_t)in_page * VECTOR_BYTES_PER_NODE;
					double distance = 0;
					for (uint32_t d = 0; d < dimension_; ++d) {
						float value;
						std::memcpy(&value, vector + d * sizeof(float), sizeof(float));
						const double delta = (double)dlm_query[d] - value;
						distance += delta * delta;
					}
					{
						nvmev_vuc_candidate candidate{};
						candidate.node_id = id;
						auto source = runtime_neighbor_source.find(id);
						candidate.source_node_id = source == runtime_neighbor_source.end() ?
							id : source->second;
						candidate.distance = (float)distance;
						scores.push_back(candidate);
					}
				}
				std::memcpy(dlm_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES,
					scores.data(), scores.size() * sizeof(nvmev_vuc_candidate));
				dma(current_ns, event.page, event.dbuf_slot, RETURN_VECTOR);
			} else if (event.kind == RETURN_VECTOR) {
				std::memcpy(dbuf_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES,
					dlm_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES, PAGE_BYTES);
				--dlm_used;
				const uint64_t work = score_ids_by_vector_page[event.page].size() * config_.candidate_update_ns_per_node;
				auto lane = std::min_element(parser_lane_free.begin(), parser_lane_free.end());
				const uint64_t start = std::max(current_ns, *lane);
				done.parser_queue_wait_ns += start - current_ns;
				done.neighbor_parse_ns += work; // ABI v1: aggregate controller service
				*lane = start + work;
				stage_events.push(StageEvent{*lane, UPDATE_VECTOR, event.page, event.dbuf_slot});
				record(VECTOR_PAGE, event.page, "candidate_update", current_ns, start, *lane, 4 * score_ids_by_vector_page[event.page].size());
			} else if (event.kind == UPDATE_VECTOR) {
				const uint8_t* scores = dbuf_slots.data() + (size_t)event.dbuf_slot * PAGE_BYTES;
				for (size_t i = 0; i < score_ids_by_vector_page[event.page].size(); ++i) {
					nvmev_vuc_candidate candidate;
					std::memcpy(&candidate, scores + i * sizeof(candidate), sizeof(candidate));
					if (candidate.distance <= request.threshold_fp32) qualified.push_back(candidate);
				}
				dbuf_slot_used[event.dbuf_slot] = 0;
				--dbuf_used;
				--dsp_tasks_pending;
				--vec_pages_pending;
			}
		}
		try_issue_reads(current_ns);
	}
	done.nand_read_ns = done.neighbor_nand_ns + done.vector_nand_ns;
	// vector_dma_ns includes both directions and per-transfer setup; NBR is parsed in DBUF.
	logical_finish_ns = std::max(logical_finish_ns, current_ns);
	std::vector<uint32_t> neighbor_ids;
	neighbor_ids.reserve(runtime_neighbor_source.size());
	for (const auto& item : runtime_neighbor_source)
		neighbor_ids.push_back(item.first);
	std::sort(neighbor_ids.begin(), neighbor_ids.end());
	if (raw_pages) neighbor_ids = std::move(raw_page_words);
	if (neighbor_ids.size() > request.neighbor_ids_capacity) {
		done.status = NVMEV_VUC_RESULT_OVERFLOW;
		done.result_valid = 0;
		return done;
	}

	std::sort(qualified.begin(), qualified.end(), [](const nvmev_vuc_candidate& a,
									const nvmev_vuc_candidate& b) {
		if (a.distance != b.distance)
			return a.distance < b.distance;
		return a.node_id < b.node_id;
	});
	if (qualified.size() > request.top_k)
		qualified.resize(request.top_k);
	if (qualified.size() > request.candidate_capacity) {
		done.status = NVMEV_VUC_RESULT_OVERFLOW;
		done.result_valid = 0;
		return done;
	}
	uint64_t stage_cursor_ns = logical_finish_ns;
	done.discovered_neighbor_count = (uint32_t)neighbor_ids.size();
	done.candidate_count = (uint32_t)qualified.size();
	done.input_bytes = sizeof(request) + request.node_list_bytes + request.query_bytes;
	done.output_bytes = neighbor_ids.size() * sizeof(uint32_t) +
		qualified.size() * sizeof(nvmev_vuc_candidate);
	if (config_.output_dma_ns_per_4k)
		done.output_dma_ns = page_count(sizeof(nvmev_vuc_pcie_result_header) + done.output_bytes, PAGE_BYTES) *
			config_.output_dma_ns_per_4k;
	stage_cursor_ns += done.output_dma_ns;
	if (c3_finish_time_ns)
		*c3_finish_time_ns = stage_cursor_ns;
	if (stage_cursor_ns - c3_start_time_ns > (uint64_t)request.timeout_ms * 1000000ULL) {
		done.status = NVMEV_VUC_TIMEOUT;
		done.result_valid = 0;
		return done;
	}
	std::copy(neighbor_ids.begin(), neighbor_ids.end(), neighbor_output);
	std::copy(qualified.begin(), qualified.end(), candidate_output);
	done.result_valid = 1;
	done.status = NVMEV_VUC_SUCCESS;
	return done;
}
