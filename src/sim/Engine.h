#ifndef ENGINE_H
#define ENGINE_H

#include <iostream>
#include <unordered_map>
#include <cstddef>
#include <stdint.h>
#include <stdio.h>
#include "Sim_Defs.h"
#include "EventTree.h"
#include "Sim_Object.h"

namespace MQSimEngine {
	class Engine
	{
		friend class EventTree;
	public:
		Engine()
		{
			this->_EventList = new EventTree;
			started = false;
		}

		~Engine() {
			delete _EventList;
		}
		
		static Engine* Instance();
		sim_time_type Time();
		Sim_Event* Register_sim_event(sim_time_type fireTime, Sim_Object* targetObject, void* parameters = NULL, int type = 0);
		void Ignore_sim_event(Sim_Event*);
			void Reset();
			void AddObject(Sim_Object* obj);
			Sim_Object* GetObject(sim_object_id_type object_id);
			void RemoveObject(Sim_Object* obj);
			void Prepare_simulation();
			bool Run_next_event();
			bool Has_pending_event();
			sim_time_type Next_event_time();
			void Fast_forward(sim_time_type time);
			void Enable_event_stats(uint64_t trace_first_events = 0);
			void Print_event_stats(FILE *out, size_t top_n = 20);
			void Start_simulation();
			void Stop_simulation();
		bool Has_started();
		bool Is_integrated_execution_mode();
	private:
		struct EventStats {
			uint64_t registered;
			uint64_t executed;
			uint64_t ignored;
			sim_time_type first_time;
			sim_time_type last_time;
			sim_time_type min_delta;
			sim_time_type max_delta;
			uint64_t delta_total;

			EventStats()
				: registered(0), executed(0), ignored(0), first_time(MAXIMUM_TIME),
				  last_time(0), min_delta(MAXIMUM_TIME), max_delta(0),
				  delta_total(0) {}
		};

		sim_time_type _sim_time;
		EventTree* _EventList;
		std::unordered_map<sim_object_id_type, Sim_Object*> _ObjectList;
		std::unordered_map<std::string, EventStats> _EventStats;
		bool event_stats_enabled;
		uint64_t event_trace_first;
		uint64_t event_trace_printed;
		uint64_t registered_event_count;
		uint64_t executed_event_count;
		uint64_t ignored_event_count;
		uint64_t run_event_batch_count;
		uint64_t same_time_batch_count;
		sim_time_type last_run_event_time;
		bool stop;
		bool started;
		static Engine* _instance;
		std::string event_key(Sim_Object *targetObject, int type);
		void record_registered_event(Sim_Event *event);
		void record_executed_event(Sim_Event *event, sim_time_type delta);
	};
}

#define Simulator MQSimEngine::Engine::Instance()
#endif // !ENGINE_H
