"use client";

import { memo, useState, useEffect, useRef, useMemo, type FC } from "react";
import { Loader2, ChevronDown, ChevronRight, CheckCircle2 } from "lucide-react";
import { useMessage } from "@assistant-ui/react";

interface StepData {
  step: string;
  status?: "started" | "completed";
  duration_ms?: number;
  is_parent?: boolean;
}

/**
 * Format duration in a human-readable way
 */
function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * StepIndicator - Shows workflow steps in a collapsible hierarchical list
 * 
 * Parent steps (executor-level) contain child steps (tool-level).
 * Shows real-time progress with spinners and checkmarks with duration.
 */
const StepIndicatorImpl: FC = () => {
  const message = useMessage();
  const [isExpanded, setIsExpanded] = useState(true);
  const wasRunningRef = useRef(false);
  const startTimeRef = useRef<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [timerStopped, setTimerStopped] = useState(false);
  
  // Find the reasoning part (which contains our steps as JSON)
  const reasoningPart = message.content.find(
    (part) => part.type === "reasoning"
  );
  
  const isRunning = message.status?.type === "running";
  
  // Parse steps from reasoning part (it's a JSON object with steps array and stepsComplete flag)
  // Use useMemo to parse only when reasoningPart changes
  const { allSteps, stepsComplete } = useMemo(() => {
    let steps: StepData[] = [];
    let complete = false;
    
    if (reasoningPart?.type === "reasoning" && reasoningPart.text) {
      try {
        const parsed = JSON.parse(reasoningPart.text);
        if (Array.isArray(parsed)) {
          // Handle old format (string[] or StepData[])
          steps = parsed.map(item => 
            typeof item === "string" 
              ? { step: item, status: "started" as const } 
              : item
          );
        } else if (parsed && typeof parsed === "object") {
          // New format: { steps: StepData[], stepsComplete: boolean }
          if (Array.isArray(parsed.steps)) {
            steps = parsed.steps.map((item: StepData | string) => 
              typeof item === "string" 
                ? { step: item, status: "started" as const } 
                : item
            );
          }
          complete = parsed.stepsComplete === true;
        }
      } catch {
        // Not JSON, might be legacy single step string
        steps = [{ step: reasoningPart.text, status: "started" }];
      }
    }
    
    return { allSteps: steps, stepsComplete: complete };
  }, [reasoningPart]);
  
  // Stop timer when stepsComplete becomes true
  useEffect(() => {
    if (stepsComplete && !timerStopped && startTimeRef.current) {
      setElapsedMs(Date.now() - startTimeRef.current);
      setTimerStopped(true);
    }
  }, [stepsComplete, timerStopped]);
  
  // Track elapsed time while running (and timer not stopped)
  useEffect(() => {
    if (isRunning && !startTimeRef.current) {
      startTimeRef.current = Date.now();
    }
    
    let intervalId: NodeJS.Timeout | null = null;
    
    // Only run timer if still running and not stopped by stepsComplete
    if (isRunning && startTimeRef.current && !timerStopped) {
      intervalId = setInterval(() => {
        setElapsedMs(Date.now() - startTimeRef.current!);
      }, 100);
    }
    
    // When done running, calculate final elapsed time (if not already stopped)
    if (!isRunning && startTimeRef.current && !timerStopped) {
      setElapsedMs(Date.now() - startTimeRef.current);
    }
    
    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [isRunning, timerStopped]);
  
  // Auto-collapse when message transitions from running to complete OR when stepsComplete
  useEffect(() => {
    if ((wasRunningRef.current && !isRunning) || stepsComplete) {
      setIsExpanded(false);
    }
    wasRunningRef.current = isRunning;
  }, [isRunning, stepsComplete]);
  
  if (allSteps.length === 0) {
    return null;
  }
  
  // Organize steps into parent/child hierarchy
  const parentSteps = allSteps.filter(s => s.is_parent);
  const childSteps = allSteps.filter(s => !s.is_parent);
  
  // Find the current active parent (last one that's started but not completed)
  const activeParent = [...parentSteps].reverse().find(s => s.status === "started");
  
  // Find current in-progress child step for header display
  const currentChildStep = childSteps.find(s => s.status !== "completed");
  
  // Complete when message is no longer running OR when stepsComplete signal is received
  // stepsComplete is sent before the stream ends, allowing early UI completion
  const allComplete = !isRunning || stepsComplete;
  
  return (
    <div className="mb-3 rounded-lg border border-border/50 bg-muted/30 overflow-hidden">
      {/* Header - always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-muted-foreground hover:bg-muted/50 transition-colors"
      >
        {isExpanded ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        
        {allComplete ? (
          <>
            <CheckCircle2 className="h-4 w-4 text-green-500" />
            <span className="flex-1 text-left">
              Completed
              {elapsedMs > 0 && (
                <span className="text-muted-foreground/70 ml-1">
                  ({formatDuration(elapsedMs)})
                </span>
              )}
            </span>
          </>
        ) : (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="flex-1 text-left truncate">
              {currentChildStep?.step || "Processing..."}
              {elapsedMs > 0 && (
                <span className="text-muted-foreground/70 ml-1">
                  ({formatDuration(elapsedMs)})
                </span>
              )}
            </span>
          </>
        )}
      </button>
      
      {/* Expandable step list - shows child steps */}
      {isExpanded && childSteps.length > 0 && (
        <div className="border-t border-border/50 px-3 py-2 space-y-1">
          {childSteps.filter(s => s.status === "completed").map((stepData, index) => (
            <div
              key={`${stepData.step}-${index}`}
              className="flex items-center gap-2 text-xs text-muted-foreground/70 pl-2"
            >
              <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
              <span className="truncate flex-1">{stepData.step}</span>
              {stepData.duration_ms !== undefined && (
                <span className="text-muted-foreground/50 tabular-nums">
                  {formatDuration(stepData.duration_ms)}
                </span>
              )}
            </div>
          ))}
          
          {childSteps.filter(s => s.status !== "completed").map((stepData, index) => (
            <div 
              key={`${stepData.step}-${index}`}
              className="flex items-center gap-2 text-xs text-foreground font-medium pl-2"
            >
              <Loader2 className="h-3 w-3 animate-spin shrink-0" />
              <span className="truncate">{stepData.step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export const StepIndicator = memo(StepIndicatorImpl);
StepIndicator.displayName = "StepIndicator";
