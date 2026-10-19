// Exercise the real MQSim event engine plus the C3 pipeline on a tiny image.
#include "../vuc_service.h"
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
static void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("usage: vuc_timing_smoke fixture-directory");
        const std::string root = argv[1];
        MQSimNativeOnlineModel model;
        std::string error;
        require(model.initialize(root + "/ssd.xml", &error), error.c_str());
        model.preload_write(0, 256);
        uint64_t finish = model.simulated_time_ns();
        for (int mode = 0; mode < 4; ++mode) {
            VUCServiceConfig config;
            config.neighbor_path = root + "/nbr";
            config.vector_path = root + "/vec";
            config.node_to_nbr_slot_path = root + "/map";
            config.node_to_vector_slot_path = root + "/vec_map";
            config.neighbor_page_lba_path = root + "/nbr_lba";
            config.vector_page_lba_path = root + "/vec_lba";
            config.trace_path = root + "/trace.csv";
            if (mode == 1) { config.dbuf_capacity_pages = 1; config.dlm_capacity_pages = 2; }
            if (mode == 2) config.dlm_capacity_pages = 2;
            if (mode == 3) {
                config.input_dma_ns_per_4k = config.output_dma_ns_per_4k = 0;
                config.idma_ns_per_4k = config.idma_setup_ns = config.ecc_ns_per_4k = 0;
                config.dsp_ns_per_float_component = config.dsp_setup_ns_per_page = 0;
                config.neighbor_parse_ns_per_id = config.candidate_update_ns_per_node = 0;
            }
            MQSimVUCService service(model, config);
            std::vector<uint8_t> buffer(65536);
            nvmev_vuc_expand_filter_request req{};
            req.abi_version = NVMEV_VUC_ABI_VERSION; req.opcode = 0xC1; req.query_id = mode; req.batch_id = 1;
            req.node_count = 4; req.top_k = 3;
            req.node_list_ref = 112; req.node_list_bytes = 16;
            req.query_ref = 128; req.query_bytes = 512;
            req.threshold_format = 1; req.threshold_fp32 = std::numeric_limits<float>::infinity();
            req.neighbor_ids_ref = 32768; req.neighbor_ids_capacity = 4096;
            req.candidates_ref = 49152; req.candidate_capacity = 1024; req.timeout_ms = 30000;
            uint32_t nodes[]{0, 8, 16, 24};
            memcpy(buffer.data() + req.node_list_ref, nodes, sizeof(nodes));
            const uint64_t start = finish;
            auto done = service.execute(req, buffer.data(), buffer.size(), start, &finish);
            require(done.status == 0 && done.result_valid, "pipeline failed");
            require(done.discovered_neighbor_count == 4 && done.candidate_count == 3, "wrong result counts");
            nvmev_vuc_candidate best{};
            memcpy(&best, buffer.data() + req.candidates_ref, sizeof(best));
            require(best.node_id == 0 && best.distance == 0, "scores changed");
            require(done.input_dma_ns == config.input_dma_ns_per_4k, "input DMA page rounding");
            require(done.output_dma_ns == config.output_dma_ns_per_4k, "output DMA page rounding");
            require(done.vector_dma_ns == 9 * (config.idma_setup_ns + config.idma_ns_per_4k), "missing DMA direction/setup");
            require(done.dsp_compute_ns == 8 * 128 * config.dsp_ns_per_float_component + 4 * config.dsp_setup_ns_per_page, "DSP work accounting");
            require(finish > start, "no modeled elapsed time");
            if (mode == 0) {
                auto phase_req=req;
                phase_req.query_id=10; phase_req.reserved0=NVMEV_VUC_PHASE_NBR_PAGES; phase_req.query_bytes=0;
                auto pages=service.execute(phase_req,buffer.data(),buffer.size(),finish,&finish);
                require(pages.result_valid && pages.discovered_neighbor_count==2*NVMEV_VUC_NBR_PAGE_WORDS,"raw NBR pages missing");
                require(pages.vector_nand_ns==0 && pages.dsp_compute_ns==0 && pages.vector_dma_ns==0,"V2 exploration read vectors");
                phase_req.query_id=11; phase_req.reserved0=NVMEV_VUC_PHASE_RERANK; phase_req.query_bytes=512;
                auto rerank=service.execute(phase_req,buffer.data(),buffer.size(),finish,&finish);
                require(rerank.result_valid && rerank.discovered_neighbor_count==0 && rerank.candidate_count==3,"V2 rerank failed");
                require(rerank.neighbor_nand_ns==0,"rerank read NBR pages");

                for (int invalid = 0; invalid < 4; ++invalid) {
                    auto bad = config;
                    if (invalid == 0) bad.max_async_reads = 32;
                    if (invalid == 1) bad.idma_units = 2;
                    if (invalid == 2) bad.dbuf_capacity_pages = 33;
                    if (invalid == 3) bad.dlm_capacity_pages = 33;
                    MQSimVUCService rejected(model, bad);
                    uint64_t ignored = finish;
                    auto result = rejected.execute(req, buffer.data(), buffer.size(), finish, &ignored);
                    require(result.status == NVMEV_VUC_INVALID_ARGUMENT, "hardware constraint not enforced");
                }
            }
            std::cout << "mode=" << mode << " elapsed_ns=" << finish-start << " dma_ns=" << done.vector_dma_ns << '\n';
        }
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
