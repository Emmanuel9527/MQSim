// In-process timing runner: same Host V2 search and packed C1/C0 payloads,
// but no Linux NVMe transport. Wall time is simulator execution overhead.
#include "vucann.h"
#include "../vuc_service.h"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <unordered_set>
using namespace vucann;
int main(int argc,char** argv) {
 try {
    if(argc!=8) throw std::runtime_error("usage: VUCV2Benchmark layout_dir queries truth ssd.xml output.csv num_queries cache_pages");
    const std::string root=argv[1];
    auto data=load_dataset(root+"/sift1m.layout.json");
    auto queries=load_queries(argv[2],argv[3],std::stoul(argv[6]),10);
    MQSimNativeOnlineModel model; std::string error;
    if(!model.initialize(argv[4],&error)) throw std::runtime_error(error);
    VUCServiceConfig config;
    config.neighbor_path=root+"/sift1m.nbr4k"; config.vector_path=root+"/sift1m.vectors4k";
    config.node_to_nbr_slot_path=root+"/sift1m.node_to_nbr_slot.u32";
    config.node_to_vector_slot_path=root+"/sift1m.node_to_vector_slot.u32";
    config.neighbor_page_lba_path=root+"/nbr_lba.u64"; config.vector_page_lba_path=root+"/vec_lba.u64";
    MQSimVUCService service(model,config);
    std::vector<uint32_t> vector_map(data.nodes);
    { std::ifstream map(config.node_to_vector_slot_path,std::ios::binary); map.seekg(8);
      if(!map.read(reinterpret_cast<char*>(vector_map.data()),std::streamsize(data.nodes)*4))
          throw std::runtime_error("cannot read vector map"); }
    const auto nbr_pages=std::filesystem::file_size(config.neighbor_path)/4096;
    const auto vec_pages=std::filesystem::file_size(config.vector_path)/4096;
    const uint64_t vector_slba=((nbr_pages*8+15)/16)*16;
    std::cerr<<"Preloading "<<nbr_pages<<" NBR / "<<vec_pages<<" vector pages, synthetic contiguous LBAs\n";
    model.preload_write(0,uint32_t(nbr_pages*8));
    model.preload_write(vector_slba,uint32_t(vec_pages*8));
    uint64_t cursor=model.simulated_time_ns();
    uint64_t nbr_elapsed=0,vec_elapsed=0,nbr_reads=0,vec_reads=0,dsp_ns=0,dma_ns=0;
    std::vector<uint8_t> result;
    NvmeClient client(512,[&](uint8_t opcode,uint8_t* buffer,uint32_t bytes,const std::vector<uint32_t>& dw)->uint64_t {
        if(opcode==1) {
            std::vector<uint8_t> slot(65536);
            if(bytes>slot.size()) throw std::runtime_error("oversize stage");
            memcpy(slot.data(),buffer,bytes);
            nvmev_vuc_expand_filter_request req{}; memcpy(&req,slot.data(),sizeof(req));
            uint64_t finish=cursor;
            auto done=service.execute(req,slot.data(),slot.size(),cursor,&finish);
            if(!done.result_valid || done.status) throw std::runtime_error("C3 status "+std::to_string(done.status));
            done.completion_time_ns=finish;
            const uint64_t elapsed=finish-cursor; cursor=finish;
            if(req.reserved0==NVMEV_VUC_PHASE_NBR_PAGES) {
                nbr_elapsed+=elapsed; nbr_reads+=done.discovered_neighbor_count/NVMEV_VUC_NBR_PAGE_WORDS;
            } else {
                vec_elapsed+=elapsed;
                std::unordered_set<uint32_t> pages;
                for(uint32_t i=0;i<req.node_count;++i) {
                    uint32_t id; memcpy(&id,slot.data()+req.node_list_ref+i*4,4);
                    pages.insert(1+vector_map.at(id)/8);
                }
                vec_reads+=pages.size();
            }
            dsp_ns+=done.dsp_compute_ns; dma_ns+=done.vector_dma_ns;
            nvmev_vuc_pcie_result_header h{}; h.completion=done; h.total_bytes=uint32_t(sizeof(h)+done.output_bytes);
            result.resize(h.total_bytes); memcpy(result.data(),&h,sizeof(h));
            const auto nbytes=done.discovered_neighbor_count*4;
            memcpy(result.data()+sizeof(h),slot.data()+req.neighbor_ids_ref,nbytes);
            memcpy(result.data()+sizeof(h)+nbytes,slot.data()+req.candidates_ref,done.candidate_count*sizeof(nvmev_vuc_candidate));
            return 0; // no ioctl wall time exists in this transport
        }
        if(opcode!=0xC0 || result.empty() || result.size()>bytes) throw std::runtime_error("invalid FETCH");
        nvmev_vuc_pcie_result_header h{}; memcpy(&h,result.data(),sizeof(h));
        if(h.completion.query_id!=(uint64_t(dw[1])<<32|dw[0]) || h.completion.batch_id!=(uint64_t(dw[3])<<32|dw[2]))
            throw std::runtime_error("FETCH correlation");
        memcpy(buffer,result.data(),result.size()); result.clear(); return 0;
    });
    std::ofstream f(argv[5]); f.exceptions(std::ios::failbit|std::ios::badbit);
    f<<std::setprecision(12)<<"query_id,page_budget,beam_width,search_L,R,cache_pages,expanded_pages,expanded_nodes,cache_hits,neighbor_commands,rerank_commands,reranked,recall_at_10,neighbor_modeled_ns,rerank_modeled_ns,backend_modeled_ns,with_assumed_controller_ns,dsp_service_ns,idma_service_ns,neighbor_reads,vector_reads,simulator_wall_ns,result_node_ids\n";
    for(uint32_t budget:{4u,8u,16u,32u,64u}) for(uint32_t r:{32u,64u,128u}) {
        PageCache cache(std::stoul(argv[7]));
        for(uint32_t qi=0;qi<queries.count;++qi) {
            nbr_elapsed=vec_elapsed=nbr_reads=vec_reads=dsp_ns=dma_ns=0;
            const auto wall_start=now_ns();
            // qid is unique across parameter combinations to avoid trace/cache ambiguity.
            const uint32_t search_l=std::min<uint32_t>(data.nodes,std::max<uint32_t>(256,r));
            auto out=search_v2(data,client,cache,(uint64_t(budget)<<48)|(uint64_t(r)<<32)|qi,
                queries.queries.data()+size_t(qi)*128,budget,15,search_l,r,10);
            const auto wall=now_ns()-wall_start;
            const auto it=queries.groundtruth.begin()+size_t(qi)*queries.groundtruth_width;
            std::unordered_set<uint32_t> truth(it,it+10); size_t hits=0;
            for(const auto& x:out.best) hits+=truth.count(x.second);
            const uint64_t backend=nbr_elapsed+vec_elapsed;
            f<<qi<<','<<budget<<",15,"<<search_l<<','<<r<<','<<cache.capacity<<','<<out.expanded_pages<<','<<out.expanded_nodes<<','
             <<out.cache_hits<<','<<out.neighbor_commands<<','<<out.rerank_commands<<','<<out.reranked<<','<<double(hits)/10<<','
             <<nbr_elapsed<<','<<vec_elapsed<<','<<backend<<','<<backend+36000*(out.neighbor_commands+out.rerank_commands)<<','
             <<dsp_ns<<','<<dma_ns<<','<<nbr_reads<<','<<vec_reads<<','<<wall<<',';
            for(size_t i=0;i<out.best.size();++i) { if(i) f<<';'; f<<out.best[i].second; }
            f<<'\n';
        }
        f.flush(); std::cerr<<"finished pages="<<budget<<" R="<<r<<'\n';
    }
    model.print_compact_stats(stderr,8);
 } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
