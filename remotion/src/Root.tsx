import React from "react";
import {Composition} from "remotion";
import {DATA} from "./generated";
import {Video} from "./Video";

export const RemotionRoot: React.FC = () => {
  const {fps, width, height, totalDuration} = DATA;
  return (
    <Composition
      id="ContentToVideo"
      component={Video}
      durationInFrames={Math.max(1, Math.round(totalDuration * fps))}
      fps={fps}
      width={width}
      height={height}
    />
  );
};
